"""Google Gemini provider, on the official `google-genai` SDK."""

import asyncio
import base64
import re
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import ProviderError, Turn

_SENTINEL = object()

# Google returns 503 "model is overloaded" often enough that a single one
# should not end a test run.
_TRANSIENT_RETRIES = 2
_RETRY_DELAYS = (1.5, 4.0)


def _is_transient(error: ProviderError) -> bool:
    return "overloaded" in str(error).lower() or "503" in str(error)


def _import_sdk():
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - install-time failure
        raise ProviderError(
            "The `google-genai` package is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return genai, types


def _parts(turn: Turn, types) -> List[Any]:
    parts: List[Any] = [{"text": turn.text}]
    if turn.image_b64:
        parts.append(types.Part.from_bytes(data=base64.b64decode(turn.image_b64), mime_type="image/png"))
    return parts


def _contents(turns: List[Turn], types) -> List[Dict[str, Any]]:
    # Gemini calls the assistant role "model".
    return [
        {"role": "model" if t.role == "assistant" else "user", "parts": _parts(t, types)}
        for t in turns
    ]


def _model_from(text: str) -> str:
    match = re.search(r"models/([\w.\-]+)", text)
    return match.group(1) if match else "the configured model"


def _translate(exc: Exception) -> ProviderError:
    """Turn the SDK's raw error dumps into something a tester can act on."""
    text = str(exc)
    if "UNAVAILABLE" in text or "503" in text or "overloaded" in text.lower():
        return ProviderError(
            "Gemini is temporarily overloaded (503). QAi retried and it is still busy — "
            "wait a moment, or switch to Claude or ChatGPT in Settings.",
            kind="rate_limit",
        )
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return ProviderError(
            "Gemini quota exhausted (429). The free tier resets on a daily and "
            "per-minute basis — wait and retry, enable billing on the key, or "
            "switch to Claude or ChatGPT in Settings.",
            kind="rate_limit",
        )
    if "API_KEY_INVALID" in text or "API key not valid" in text or "PERMISSION_DENIED" in text:
        return ProviderError("Gemini rejected the API key.", kind="auth")
    if "NOT_FOUND" in text or "is not found for API version" in text:
        # Google's message usually names the replacement model. Keeping it is
        # far more useful than the generic "model not recognised".
        detail = ""
        match = re.search(r"'message':\s*'([^']+)'", text) or re.search(r'"message":\s*"([^"]+)"', text)
        if match:
            detail = " " + match.group(1)
        return ProviderError(
            f"Gemini rejected the model '{_model_from(text)}'.{detail} "
            "Use the model list in Settings to pick one this key can reach."
        )
    if "SAFETY" in text or "blocked" in text.lower():
        return ProviderError(
            "Gemini blocked this request with a safety filter. Try turning Vision "
            "off, or rewording the scenario.",
            kind="refusal",
        )
    return ProviderError(f"Gemini API error: {text[:300]}")


async def _bridge(make_iterator) -> AsyncIterator[str]:
    """Run a blocking generator on a worker thread and yield its chunks.

    The SDK's streaming iterator is synchronous; iterating it directly on the
    event loop would stall every other request for the length of the response.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()

    def pump():
        try:
            for chunk in make_iterator():
                text = getattr(chunk, "text", None)
                if text:
                    asyncio.run_coroutine_threadsafe(queue.put(text), loop).result()
        except Exception as exc:  # surfaced on the consumer side
            asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(_SENTINEL), loop).result()

    task = asyncio.create_task(asyncio.to_thread(pump))
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise _translate(item)
            yield item
    finally:
        await task


class GeminiProvider:
    id = "gemini"
    label = "Gemini (Google)"

    async def stream(
        self,
        system: str,
        turns: List[Turn],
        model: str,
        api_key: str,
        effort: str = "medium",
        # Accepted for protocol compatibility; this provider is not wired to
        # report token usage, so the sink is left untouched.
        usage: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        # Accepted and ignored: this SDK exposes no equivalent dial here, and
        # refusing the run over a knob the provider lacks would be worse than
        # quietly running at its own pace.
        del effort
        genai, types = _import_sdk()
        try:
            client = genai.Client(api_key=api_key)
        except Exception as exc:
            raise _translate(exc) from exc

        config = types.GenerateContentConfig(system_instruction=system, temperature=0.2)
        contents = _contents(turns, types)

        def make_iterator():
            return client.models.generate_content_stream(model=model, contents=contents, config=config)

        # "Model overloaded" is transient and common enough that letting it kill
        # a whole run is wasteful. Retry only while nothing has been emitted —
        # once tokens are out, restarting would duplicate them.
        for attempt in range(_TRANSIENT_RETRIES + 1):
            produced = False
            try:
                async for text in _bridge(make_iterator):
                    produced = True
                    yield text
                return
            except ProviderError as exc:
                last = exc
                if produced or attempt == _TRANSIENT_RETRIES or not _is_transient(exc):
                    raise
                await asyncio.sleep(_RETRY_DELAYS[attempt])
        raise last

    async def list_models(self, api_key: str) -> List[str]:
        """What this key can actually reach.

        A hardcoded list goes stale the moment a provider retires a model —
        which is exactly how a saved config starts failing with "model not
        found" for a model that was fine last month.
        """
        genai, _ = _import_sdk()

        def call():
            client = genai.Client(api_key=api_key)
            return [m.name.removeprefix("models/") for m in client.models.list()]

        try:
            names = await asyncio.to_thread(call)
        except Exception as exc:
            raise _translate(exc) from exc

        # Only the text-generation families are useful here.
        return sorted(
            n for n in names
            if any(n.startswith(prefix) for prefix in ("gemini", "gemma"))
            and not any(bad in n for bad in ("embedding", "tts", "image", "vision-latest", "aqa"))
        )

    async def check(self, model: str, api_key: str) -> str:
        genai, _ = _import_sdk()

        def call():
            client = genai.Client(api_key=api_key)
            return client.models.generate_content(
                model=model, contents="Reply with the single word: ready"
            )

        try:
            response = await asyncio.to_thread(call)
        except Exception as exc:
            raise _translate(exc) from exc
        return (response.text or "")[:60]
