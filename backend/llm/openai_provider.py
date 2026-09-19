"""OpenAI provider, on the official `openai` SDK."""

from typing import Any, AsyncIterator, Dict, List, Optional

from .base import ProviderError, Turn, image_media_type


def _import_sdk():
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - install-time failure
        raise ProviderError(
            "The `openai` package is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return openai


def _explain_status(exc) -> ProviderError:
    """OpenAI returns 429 both for "too fast" and "out of credit" — very
    different problems, and waiting only fixes one of them."""
    message = getattr(exc, "message", None) or str(exc)
    if "insufficient_quota" in message or "exceeded your current quota" in message:
        return ProviderError(
            "The OpenAI account is out of credit. A new key gets no free "
            "allowance — add a payment method at platform.openai.com/billing, "
            "or switch provider in Settings.",
            kind="auth",
        )
    if "model_not_found" in message or "does not exist" in message:
        return ProviderError(
            "OpenAI does not recognise that model, or this key has no access to it. "
            "Use 'Load available models' in Settings to see what the key can reach."
        )
    status = getattr(exc, "status_code", None)
    return ProviderError(f"OpenAI API error{f' ({status})' if status else ''}: {message[:300]}")


def _content(turn: Turn) -> Any:
    if not turn.image_b64:
        return turn.text
    return [
        {"type": "text", "text": turn.text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image_media_type(turn.image_b64)};base64,{turn.image_b64}"},
        },
    ]


def _messages(system: str, turns: List[Turn]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend({"role": t.role, "content": _content(t)} for t in turns)
    return messages


class OpenAIProvider:
    id = "openai"
    label = "ChatGPT (OpenAI)"

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
        # Accepted and ignored: chat completions take no reasoning-effort here,
        # and refusing the run over a knob the provider lacks would be worse
        # than quietly running at its own pace.
        del effort
        openai = _import_sdk()
        client = openai.AsyncOpenAI(api_key=api_key)

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=_messages(system, turns),
                temperature=0.2,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except openai.AuthenticationError as exc:
            raise ProviderError("OpenAI rejected the API key.", kind="auth") from exc
        except openai.RateLimitError as exc:
            raise _explain_status(exc) from exc
        except openai.NotFoundError as exc:
            raise ProviderError(
                f"OpenAI does not recognise the model '{model}', or your key has no access to it."
            ) from exc
        except openai.APIStatusError as exc:
            raise _explain_status(exc) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("Could not reach the OpenAI API. Check the network.") from exc
        finally:
            await client.close()

    async def list_models(self, api_key: str) -> List[str]:
        openai = _import_sdk()
        client = openai.AsyncOpenAI(api_key=api_key)
        try:
            page = await client.models.list()
            names = [m.id for m in page.data]
        except openai.AuthenticationError as exc:
            raise ProviderError("OpenAI rejected the API key.", kind="auth") from exc
        except openai.APIStatusError as exc:
            raise _explain_status(exc) from exc
        finally:
            await client.close()

        # The account's model list includes embeddings, audio and image models
        # the agent cannot use.
        return sorted(
            n for n in names
            if any(n.startswith(prefix) for prefix in ("gpt-", "o1", "o3", "o4", "chatgpt"))
            and not any(bad in n for bad in ("audio", "realtime", "transcribe", "tts", "image", "search"))
        )

    async def check(self, model: str, api_key: str) -> str:
        openai = _import_sdk()
        client = openai.AsyncOpenAI(api_key=api_key)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with the single word: ready"}],
                max_completion_tokens=16,
            )
            return (response.choices[0].message.content or "")[:60]
        except openai.AuthenticationError as exc:
            raise ProviderError("OpenAI rejected the API key.", kind="auth") from exc
        except openai.NotFoundError as exc:
            raise ProviderError(
                f"OpenAI does not recognise the model '{model}', or your key has no access to it."
            ) from exc
        except openai.APIStatusError as exc:
            raise _explain_status(exc) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("Could not reach the OpenAI API. Check the network.") from exc
        finally:
            await client.close()
