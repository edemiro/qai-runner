"""The provider catalogue and the active-provider selection.

Keys are stored per provider, so switching between them does not discard the
key you already saved for the other two.
"""

import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from config import ENV_PATH, write_env
from .base import LLMProvider, ProviderError
from .claude import ClaudeProvider
from .gemini import GeminiProvider
from .openai_provider import OpenAIProvider

_PROVIDERS: Dict[str, LLMProvider] = {
    p.id: p for p in (GeminiProvider(), ClaudeProvider(), OpenAIProvider())
}

# Suggested models per provider. These are starting points, not a whitelist —
# the UI lets you type any model id, because every provider's lineup moves
# faster than a hardcoded list can follow.
CATALOG: List[Dict[str, Any]] = [
    {
        "id": "gemini",
        "label": "Gemini (Google)",
        "envKey": "GEMINI_API_KEY",
        "keyHint": "AIzaSy…",
        "consoleUrl": "https://aistudio.google.com/apikey",
        # Google retires model ids for new keys, so the defaults are the
        # rolling aliases rather than a pinned version that goes stale.
        "defaultModel": "gemini-flash-latest",
        "models": [
            {"id": "gemini-flash-latest", "note": "Fast and cheap — a good default for step-by-step driving"},
            {"id": "gemini-pro-latest", "note": "Slower, stronger reasoning for tangled flows"},
            {"id": "gemini-flash-lite-latest", "note": "Cheapest; fine for simple flows"},
        ],
    },
    {
        "id": "claude",
        "label": "Claude (Anthropic)",
        "envKey": "ANTHROPIC_API_KEY",
        "keyHint": "sk-ant-…",
        "consoleUrl": "https://console.anthropic.com/settings/keys",
        "defaultModel": "claude-opus-5",
        # Identity-linked keys are not bound to a workspace, so Anthropic
        # rejects them unless the request names one. Workspace-scoped keys
        # ignore this, which is why it is optional.
        "extra": {
            "envKey": "ANTHROPIC_WORKSPACE_ID",
            "label": "Workspace ID",
            "hint": "wrkspc_…",
            "help": "Only needed for identity-linked keys. Console → Settings → Workspaces.",
            "optional": True,
        },
        "models": [
            {"id": "claude-opus-5", "note": "Most capable — best at recovering from unexpected screens"},
            {"id": "claude-sonnet-5", "note": "Cheaper, still strong on long runs"},
            {"id": "claude-haiku-4-5", "note": "Fastest and cheapest, for simple flows"},
        ],
    },
    {
        "id": "openai",
        "label": "ChatGPT (OpenAI)",
        "envKey": "OPENAI_API_KEY",
        "keyHint": "sk-…",
        "consoleUrl": "https://platform.openai.com/api-keys",
        "defaultModel": "gpt-4o",
        "models": [
            {"id": "gpt-4o", "note": "Vision-capable general model"},
            {"id": "gpt-4o-mini", "note": "Cheaper and faster"},
            {"id": "gpt-4.1", "note": "Stronger reasoning"},
            {"id": "gpt-4.1-mini", "note": "Balanced cost and capability"},
        ],
    },
]

_BY_ID = {entry["id"]: entry for entry in CATALOG}
DEFAULT_PROVIDER = "gemini"


def catalog() -> List[Dict[str, Any]]:
    """The provider list, annotated with which keys are already saved."""
    out = []
    for entry in CATALOG:
        item = {**entry, "configured": bool(os.environ.get(entry["envKey"], "").strip())}
        extra = entry.get("extra")
        if extra:
            # Echo the value back: unlike a key, a workspace id is not a secret
            # and showing it saves the user a trip to the console.
            item["extra"] = {**extra, "value": os.environ.get(extra["envKey"], "").strip()}
        out.append(item)
    return out


@contextmanager
def temporary_extra(provider_id: str, value: Optional[str]) -> Iterator[None]:
    """Apply an unsaved extra setting for the duration of one call.

    Lets "Test connection" validate the workspace id the user just typed,
    instead of silently testing the previously saved one.
    """
    spec = _BY_ID.get(provider_id, {}).get("extra")
    if spec is None or value is None:
        yield
        return

    key = spec["envKey"]
    previous = os.environ.get(key)
    os.environ[key] = value.strip()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def env_key_name(provider_id: str) -> str:
    entry = _BY_ID.get(provider_id)
    if entry is None:
        raise ProviderError(f"Unknown provider '{provider_id}'.")
    return entry["envKey"]


def api_key_for(provider_id: str) -> str:
    return os.environ.get(env_key_name(provider_id), "").strip()


def default_model(provider_id: str) -> str:
    entry = _BY_ID.get(provider_id)
    return entry["defaultModel"] if entry else ""


def suggested_models(provider_id: str) -> List[Dict[str, str]]:
    entry = _BY_ID.get(provider_id)
    return entry["models"] if entry else []


def active_provider_id() -> str:
    provider_id = os.environ.get("LLM_PROVIDER", "").strip()
    return provider_id if provider_id in _PROVIDERS else DEFAULT_PROVIDER


def active_model() -> str:
    model = os.environ.get("LLM_MODEL", "").strip()
    if model:
        return model
    # Migration path: earlier versions stored a bare Gemini model here.
    legacy = os.environ.get("DEFAULT_MODEL", "").strip()
    if legacy and active_provider_id() == "gemini":
        return legacy
    return default_model(active_provider_id())


def get(provider_id: Optional[str] = None) -> LLMProvider:
    provider_id = provider_id or active_provider_id()
    provider = _PROVIDERS.get(provider_id)
    if provider is None:
        raise ProviderError(f"Unknown provider '{provider_id}'.")
    return provider


def is_configured() -> bool:
    return bool(api_key_for(active_provider_id()))


def status() -> Dict[str, Any]:
    provider_id = active_provider_id()
    entry = _BY_ID.get(provider_id, {})
    return {
        "provider": provider_id,
        "providerLabel": entry.get("label", provider_id),
        "model": active_model(),
        "configured": is_configured(),
        "envPath": ENV_PATH,
    }


def save(
    provider_id: str,
    model: Optional[str],
    api_key: Optional[str],
    extra: Optional[str] = None,
) -> None:
    """Persist the selection, and the key when one was supplied.

    An empty key is not an erase — it means "keep what is already saved", which
    is what the UI sends when you switch models without retyping the key.
    """
    if provider_id not in _PROVIDERS:
        raise ProviderError(f"Unknown provider '{provider_id}'.")

    updates = {
        "LLM_PROVIDER": provider_id,
        "LLM_MODEL": (model or "").strip() or default_model(provider_id),
    }
    if api_key and api_key.strip():
        updates[env_key_name(provider_id)] = api_key.strip()

    # An empty extra field is a deliberate clear — unlike a key, where empty
    # means "keep what is saved" because the UI never shows it back.
    extra_spec = _BY_ID[provider_id].get("extra")
    if extra_spec is not None and extra is not None:
        updates[extra_spec["envKey"]] = extra.strip()

    if not (api_key and api_key.strip()) and not api_key_for(provider_id):
        raise ProviderError(
            f"No API key saved for {_BY_ID[provider_id]['label']}. Enter one before selecting it."
        )

    write_env(updates)
