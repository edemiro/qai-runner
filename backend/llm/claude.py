"""Anthropic Claude provider, on the official `anthropic` SDK."""

import asyncio
import os
import re
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import ProviderError, Turn, add_usage, image_media_type

# Models whose refusals can be rescued by the server-side fallback chain.
# Matched by prefix, so a dated or context-suffixed id — claude-opus-5-5,
# claude-fable-5-1, claude-opus-5[1m] — is recognised without this list
# having to name every variant the lineup grows.
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5")

# Thinking counts against max_tokens, and adaptive thinking is on by default on
# Opus 5, so leave real headroom even though an agent turn is only a sentence
# plus a small JSON block.
_MAX_TOKENS = 16000

# A rate limit is a wait, not a verdict. Measured on the hotel set: three
# scenarios in parallel ran into the account's per-minute limit, and sixteen of
# them ended on "rate limit reached" within four minutes — an execution's
# results thrown away for want of waiting. The SDK retries twice on its own,
# within seconds; this waits longer, and only before anything has streamed, so
# nothing is said twice. The API's own `retry-after` wins when it gives one.
RATE_LIMIT_WAITS = (10.0, 20.0, 40.0, 60.0)


def _budget_spent(exc: Any) -> Optional[str]:
    """What the gateway said, when a 429 is its spending cap and not a rate.

    A corporate gateway answers "budget_exceeded" with the same 429 as a rate
    limit, and no wait lifts it. Read as a rate limit, the hotel re-run spent
    two and a half minutes of retries on every scenario and then filed each one
    as "rate limit reached" — against a cap of 201 that had been passed.
    """
    said = getattr(exc, "message", None) or str(exc) or ""
    if "budget_exceeded" not in said and "budget has been exceeded" not in said.lower():
        return None
    spent = re.search(r"Current cost:\s*([\d.]+),\s*Max budget:\s*([\d.]+)", said)
    if spent:
        return f"spent {float(spent.group(1)):.2f} of {float(spent.group(2)):.2f}"
    return "the budget is used up"


def _retry_after(exc: Any) -> Optional[float]:
    """How long the API asked for, when it said."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = headers.get(name) if hasattr(headers, "get") else None
        if not value:
            continue
        try:
            return max(1.0, min(float(value) / scale, 90.0))
        except (TypeError, ValueError):
            continue
    return None


def _import_sdk():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - install-time failure
        raise ProviderError(
            "The `anthropic` package is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return anthropic


def _client(anthropic, api_key: str):
    """Build the client, carrying the workspace header when one is configured.

    Identity-linked API keys are not bound to a single workspace, so Anthropic
    rejects them unless the request says which workspace it acts in. Classic
    workspace-scoped keys need no header, so this stays optional.

    ANTHROPIC_BASE_URL points the SDK at a corporate gateway (an Azure-fronted
    proxy, say) that speaks the Anthropic API but lives behind the company
    network — the key is that gateway's, not Anthropic's, so a direct call to
    api.anthropic.com is what gets rejected. Empty means talk to Anthropic.
    """
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    kwargs: Dict[str, Any] = {
        "default_headers": headers,
        # A stalled gateway must surface as an error, not an open connection
        # the SDK waits on for its default ten minutes. A plain number of
        # seconds: this SDK build bundles its own httpx ("httpx2") and rejects
        # a Timeout object from the other one, so no object is passed at all.
        "timeout": 300.0,
    }
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        # A gateway authenticates with a bearer token, not Anthropic's x-api-key.
        # Passing auth_token (not api_key) sends `Authorization: Bearer <key>` and
        # stops the SDK from also reading ANTHROPIC_API_KEY and adding x-api-key,
        # which the gateway would reject.
        kwargs["base_url"] = base_url
        kwargs["auth_token"] = api_key
    else:
        kwargs["api_key"] = api_key
    return anthropic.AsyncAnthropic(**kwargs)


def _explain(anthropic, exc) -> ProviderError:
    """Translate the errors a tester can actually do something about."""
    message = getattr(exc, "message", None) or str(exc)
    if "anthropic-workspace-id" in message:
        return ProviderError(
            "This Anthropic key is identity-linked, so it needs a workspace id. "
            "Open console.anthropic.com → Settings → Workspaces, copy the id of the "
            "workspace this key belongs to (it starts with `wrkspc_`), and paste it "
            "into the Workspace ID field in Settings.",
            kind="auth",
        )
    if "credit balance is too low" in message or "billing" in message.lower():
        return ProviderError(
            "The Anthropic account has no credit. Add credit in the console, or "
            "switch provider in Settings.",
            kind="auth",
        )
    status = getattr(exc, "status_code", None)
    return ProviderError(f"Anthropic API error{f' ({status})' if status else ''}: {message[:300]}")


def _content(turn: Turn) -> Any:
    if not turn.image_b64:
        return turn.text
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type(turn.image_b64),
                "data": turn.image_b64,
            },
        },
        {"type": "text", "text": turn.text},
    ]


def _blocks(content: Any) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def _messages(turns: List[Turn]) -> List[Dict[str, Any]]:
    messages = [{"role": t.role, "content": _content(t)} for t in turns]

    # Everything except the newest screen is settled history: a run appends to
    # it and never rewrites it, which is exactly the shape a cache prefix wants.
    # Marking its final block means step N+1 reads steps 1..N instead of paying
    # for them again, and the cost grows with the run instead of per step. The
    # newest turn stays outside the marker — its tree and frame are new every
    # step and would only invalidate the entry.
    if len(messages) > 1:
        history_end = messages[-2]
        history_end["content"] = _blocks(history_end["content"])
        history_end["content"][-1]["cache_control"] = {"type": "ephemeral"}

    return messages


def _request(system: str, turns: List[Turn], model: str, effort: str = "medium") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        # The system prompt is byte-identical on every step of a run, so it is
        # the one part of the request worth caching: a cache read costs about a
        # tenth of a fresh read. It has to be a block, not a bare string, to
        # carry the marker. Opus 5 caches prefixes from 512 tokens up and this
        # prompt is comfortably past that; a shorter prompt would silently not
        # cache rather than error.
        "system": [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": _messages(turns),
        # Picking one correct element out of a live UI tree is exactly the kind
        # of small judgement adaptive thinking is for. How hard to think about
        # it is the run's call — a crowded screen repays "high", a plain form
        # does not — so effort arrives per run rather than being fixed here.
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
    }
    # Note: temperature/top_p are rejected on Opus 5 and the 4.6+ family, so the
    # sampling knobs the other providers use deliberately have no analogue here.
    return payload


def _gateway_mode() -> bool:
    return bool(os.environ.get("ANTHROPIC_BASE_URL", "").strip())


def _wants_fallback(model: str) -> bool:
    # A corporate gateway generally does not carry Anthropic's beta features, so
    # the server-side-fallback beta is skipped there — it would just be rejected.
    if _gateway_mode():
        return False
    return any(model.startswith(prefix) for prefix in _FALLBACK_MODELS)


class ClaudeProvider:
    id = "claude"
    label = "Claude (Anthropic)"

    async def stream(
        self,
        system: str,
        turns: List[Turn],
        model: str,
        api_key: str,
        effort: str = "medium",
        usage: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        anthropic = _import_sdk()
        client = _client(anthropic, api_key)

        payload = _request(system, turns, model, effort)
        use_beta = _wants_fallback(model)
        if use_beta:
            # A safety decline would otherwise just stop the run mid-scenario;
            # the server re-runs the same request on a fallback model in-call.
            payload["betas"] = ["server-side-fallback-2026-07-01"]
            payload["fallbacks"] = "default"

        endpoint = client.beta.messages if use_beta else client.messages

        try:
            for attempt in range(len(RATE_LIMIT_WAITS) + 1):
                produced = False
                try:
                    async with endpoint.stream(**payload) as stream:
                        async for text in stream.text_stream:
                            produced = True
                            yield text
                        final = await stream.get_final_message()
                    break
                except anthropic.RateLimitError as exc:
                    budget = _budget_spent(exc)
                    if budget:
                        raise ProviderError(
                            f"The model gateway's spending budget is used up ({budget}). "
                            "Waiting will not lift it: raise the budget or wait for it "
                            "to reset, then run again.",
                            kind="budget",
                        ) from exc
                    if produced or attempt == len(RATE_LIMIT_WAITS):
                        raise ProviderError(
                            "Anthropic rate limit reached. Wait and retry.", kind="rate_limit",
                        ) from exc
                    wait = _retry_after(exc) or RATE_LIMIT_WAITS[attempt]
                    print(f"[claude] rate limited; trying again in {wait:.0f}s "
                          f"({attempt + 1} of {len(RATE_LIMIT_WAITS)})")
                    await asyncio.sleep(wait)
        except anthropic.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the API key.", kind="auth") from exc
        except anthropic.APIStatusError as exc:
            raise _explain(anthropic, exc) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Could not reach the Anthropic API. Check the network.") from exc
        finally:
            await client.close()

        # Cache reads and writes are counted apart from fresh input: a run whose
        # prompt is mostly a cache hit costs a fraction of what the raw input
        # figure suggests, and hiding that would make the totals unreadable.
        counts = getattr(final, "usage", None)
        if counts is not None:
            add_usage(
                usage,
                input_tokens=getattr(counts, "input_tokens", 0) or 0,
                output_tokens=getattr(counts, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(counts, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(counts, "cache_creation_input_tokens", 0) or 0,
            )

        if final.stop_reason == "refusal":
            detail = getattr(final, "stop_details", None)
            category = getattr(detail, "category", None) or "unspecified"
            raise ProviderError(
                f"Claude declined this step (category: {category}). "
                "Rephrase the scenario, or drive this step manually from the inspector.",
                kind="refusal",
            )

    async def list_models(self, api_key: str) -> List[str]:
        anthropic = _import_sdk()
        client = _client(anthropic, api_key)
        try:
            page = await client.models.list(limit=100)
            return [m.id for m in page.data]
        except anthropic.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the API key.", kind="auth") from exc
        except anthropic.APIStatusError as exc:
            raise _explain(anthropic, exc) from exc
        finally:
            await client.close()

    async def check(self, model: str, api_key: str) -> str:
        anthropic = _import_sdk()
        client = _client(anthropic, api_key)
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=64,
                messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            )
            return next((b.text for b in response.content if b.type == "text"), "")[:60]
        except anthropic.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the API key.", kind="auth") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"Anthropic does not recognise the model '{model}'.") from exc
        except anthropic.APIStatusError as exc:
            raise _explain(anthropic, exc) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Could not reach the Anthropic API. Check the network.") from exc
        finally:
            await client.close()
