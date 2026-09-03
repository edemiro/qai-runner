"""Self-healing replay: deterministic first, model-assisted only when it breaks.

A recorded run is cheap and repeatable to replay — no model call, no token
cost, no nondeterminism — right up until the page changes and a selector stops
matching. Classic Selenium and Appium suites die exactly there, and someone has
to go re-record the test.

QAi already has both halves of the answer: a recording to replay, and a model
that can find an element from a description. This module joins them. Each step
tries, in order:

  1. the selector recorded last time (free, instant);
  2. the same element described semantically, re-matched against the current
     page (free, no model);
  3. the model, given the step's intent and the current screen (costs a call).

Whatever finally worked is written back to the step, so the *next* replay
starts from the healed selector and the cost is paid once, not every run.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from llm.base import ProviderError, Turn

# Healing one step is a small, closed question, so a short budget is enough and
# keeps a broken suite from turning into a large bill.
HEAL_SYSTEM_PROMPT = """You are repairing a UI test after the page it targets was redesigned.

A recorded step's selector no longer matches. You get the step's original
intent and the page's current element tree. Your job is to identify which
element is now playing the role the original element played.

You are being asked precisely BECAUSE the page changed. Renamed labels,
regenerated class names, new wrapper elements and reordered layouts are the
expected case, not evidence that the control is gone. A button that has been
relabelled is still the same button.

Reply with ONLY a JSON object, no prose:
{"elementId": "e12", "confidence": 0.0-1.0, "reason": "why this is the same control"}

How to decide:
- Ask "if a person opened this page to do the thing the step describes, what
  would they click?" That element is the answer.
- Weigh role, position, and purpose above exact wording. Text is the weakest
  signal here, because text is what usually changed.
- If exactly one element can plausibly serve the original purpose, return it,
  even when the label is completely different.
- Pick from the elementIds present in the tree. Never invent one.

Calibrating confidence — this is a question about THIS page, not about UI in
general, so answer it relative to the alternatives actually present:
- 0.9-1.0: the label still matches, or a stable id or test id matches.
- 0.7-0.9: the label changed, but the element has the same role and position
  and is the only control that could serve the step's purpose. Being the sole
  candidate is strong evidence, not weak evidence — if a person had to click
  something to do this, they would click this.
- 0.4-0.7: several elements could serve the purpose and you are choosing
  between them, or you are inferring from position alone.
- Below 0.4: you are guessing.

Do not discount your confidence merely because the wording changed. The
rewording is the reason you were called.

Return null ONLY when the page genuinely cannot serve the step at all — for
example the step types into a field and the page has no input, or the flow has
moved to a different screen entirely:
{"elementId": null, "confidence": 0, "reason": "what is missing"}"""

# Below this the healed match is reported but not acted on: silently clicking
# something that merely resembles the original is worse than failing.
MIN_CONFIDENCE = 0.55


@dataclass
class HealResult:
    ok: bool
    strategy: str  # recorded | semantic | model | none
    message: str
    element_id: Optional[str] = None
    selector: Optional[str] = None
    confidence: Optional[float] = None
    healed: bool = False
    element: Optional[Dict[str, Any]] = None


@dataclass
class HealStats:
    """What a whole replay had to repair, for the run summary."""
    attempted: int = 0
    healed: int = 0
    failed: int = 0
    details: List[Dict[str, Any]] = field(default_factory=list)

    def record(self, step_idx: int, result: HealResult) -> None:
        self.attempted += 1
        if result.healed and result.ok:
            self.healed += 1
        elif not result.ok:
            self.failed += 1
        self.details.append({
            "step": step_idx,
            "strategy": result.strategy,
            "ok": result.ok,
            "healed": result.healed,
            "confidence": result.confidence,
            "message": result.message,
        })


def describe_intent(step: Dict[str, Any]) -> str:
    """A human-readable sentence for what the recorded step was trying to do.

    This is what the model is asked to re-find, so it deliberately leans on
    meaning (label, role, purpose) rather than on the selector that just broke.
    """
    element = step.get("element") or {}
    action = (step.get("action") or "act").lower()
    label = (
        element.get("label")
        or element.get("text")
        or element.get("content-desc")
        or step.get("target")
        or "an element"
    )
    parts = [f'{action} the element labelled "{label}"']
    if element.get("role"):
        parts.append(f'role: {element["role"]}')
    if element.get("id"):
        parts.append(f'original id: {element["id"]}')
    if step.get("value") and action in ("type", "assert_text"):
        parts.append(f'value: "{step["value"]}"')
    if element.get("href"):
        parts.append(f'link target: {element["href"]}')
    if step.get("reason"):
        parts.append(f'recorded reason: {step["reason"]}')
    return "; ".join(parts)


async def _selector_matches(target, selector: Optional[str]) -> bool:
    """Is the recorded selector still resolvable on the current page?

    Only the web driver can answer cheaply; on mobile the semantic pass below
    is the fast path anyway.
    """
    if not selector or not hasattr(target, "page"):
        return False
    try:
        return await target.page.locator(selector).first.count() > 0
    except Exception:
        return False


def _semantic_match(snapshot, step: Dict[str, Any]) -> Optional[Any]:
    """Re-find the element by what it is, without asking a model.

    Most breakage is cosmetic — a regenerated class name, a shifted index — and
    the element is still there under the same role and label. Catching that
    here means the model is only ever consulted for a genuine redesign.
    """
    if snapshot is None:
        return None

    element_data = step.get("element") or {}
    want_label = _normalise(
        element_data.get("label") or element_data.get("text") or element_data.get("content-desc") or ""
    )
    want_role = (element_data.get("role") or "").lower()
    want_id = element_data.get("id")

    try:
        candidates = snapshot.get_all_elements()
    except Exception:
        return None

    # A stable id beats everything else, including the selector.
    if want_id:
        for element in candidates:
            if getattr(element, "resource_id", None) == want_id:
                return element

    if not want_label:
        return None

    scored = []
    for element in candidates:
        label = _normalise(getattr(element, "text", "") or getattr(element, "name", "") or "")
        if not label:
            continue
        role = (getattr(element, "role", "") or "").lower()
        score = _similarity(want_label, label)
        if want_role and role == want_role:
            score += 0.15
        elif want_role and role != want_role:
            score -= 0.1
        scored.append((score, element))

    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]

    # Two equally good matches mean the choice is a guess; defer to the model
    # rather than picking the first one and calling it a repair.
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.82 and best_score - runner_up >= 0.08:
        return best
    return None


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _similarity(a: str, b: str) -> float:
    """Token overlap, which survives reordering and added punctuation better
    than an edit distance over the whole string."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    tokens_a = set(re.findall(r"\w+", a))
    tokens_b = set(re.findall(r"\w+", b))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def parse_heal_reply(text: str) -> Dict[str, Any]:
    """Pull the repair decision out of the model's reply."""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.DOTALL)
    if not blocks:
        blocks = re.findall(r'\{[^{}]*"elementId"\s*:.*?\}', text or "", re.DOTALL)
    for raw in reversed(blocks):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "elementId" in parsed:
            return parsed
    return {"elementId": None, "confidence": 0, "reason": "The model did not answer in the expected shape."}


async def _ask_model(provider, model: str, api_key: str, intent: str, screen_json: str) -> Dict[str, Any]:
    turns = [Turn(
        role="user",
        text=(
            f"ORIGINAL STEP INTENT:\n{intent}\n\n"
            f"CURRENT PAGE (element tree):\n```json\n{screen_json}\n```"
        ),
    )]
    chunks: List[str] = []
    async for chunk in provider.stream(HEAL_SYSTEM_PROMPT, turns, model, api_key):
        chunks.append(chunk)
    return parse_heal_reply("".join(chunks))


async def resolve_step(
    target,
    snapshot,
    step: Dict[str, Any],
    llm: Optional[Dict[str, Any]] = None,
) -> HealResult:
    """Work out how to perform `step` against the page as it is now.

    `llm` is {"provider":…, "model":…, "api_key":…}; without it, healing stops
    after the free semantic pass — which is what a run with no configured
    provider, or an explicit --no-heal, should do.
    """
    element_data = step.get("element") or {}
    recorded_selector = element_data.get("xpath") or step.get("selector")

    # 1. The recorded selector, if it still resolves.
    if await _selector_matches(target, recorded_selector):
        return HealResult(
            ok=True, strategy="recorded", message="Recorded selector still matches.",
            selector=recorded_selector,
        )

    # 2. The same element, re-matched semantically. Free, and covers most drift.
    match = _semantic_match(snapshot, step)
    if match is not None:
        return HealResult(
            ok=True, strategy="semantic", healed=True,
            message=f'Re-matched "{match.describe()}" by role and label.',
            element_id=getattr(match, "element_id", None),
            selector=getattr(match, "selector", None),
            confidence=0.85,
        )

    # On mobile there is no cheap selector probe, so a recorded selector that
    # the semantic pass could not confirm is still worth trying before paying
    # for a model call.
    if recorded_selector and not hasattr(target, "page"):
        return HealResult(
            ok=True, strategy="recorded", message="Trying the recorded selector.",
            selector=recorded_selector,
        )

    if not llm:
        return HealResult(
            ok=False, strategy="none",
            message=(
                "The recorded element is gone and could not be re-matched. "
                "Configure a provider to let QAi repair the step automatically."
            ),
        )

    # 3. Ask the model, giving it the intent rather than the broken selector.
    if snapshot is None:
        return HealResult(ok=False, strategy="none", message="Could not read the page to repair against.")

    intent = describe_intent(step)
    screen_json = json.dumps(
        snapshot.get_optimized_tree_for_llm(), ensure_ascii=False, separators=(",", ":")
    )

    try:
        decision = await _ask_model(llm["provider"], llm["model"], llm["api_key"], intent, screen_json)
    except ProviderError as exc:
        return HealResult(ok=False, strategy="model", message=f"Repair failed: {exc}")
    except Exception as exc:  # noqa: BLE001 — a failed repair must not kill the run
        return HealResult(ok=False, strategy="model", message=f"Repair failed: {exc}")

    element_id = decision.get("elementId")
    try:
        confidence = float(decision.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    reason = decision.get("reason") or ""

    if not element_id:
        return HealResult(
            ok=False, strategy="model", confidence=confidence,
            message=f"No element on this page fulfils the step. {reason}".strip(),
        )

    element = snapshot.elements_by_id.get(element_id)
    if element is None:
        return HealResult(
            ok=False, strategy="model", confidence=confidence,
            message=f"The repair pointed at '{element_id}', which is not on this page.",
        )

    if confidence < MIN_CONFIDENCE:
        return HealResult(
            ok=False, strategy="model", confidence=confidence, element_id=element_id,
            message=(
                f'Only {confidence:.0%} sure that "{element.describe()}" is the same control, '
                f"so the step was not repaired automatically. {reason}".strip()
            ),
        )

    return HealResult(
        ok=True, strategy="model", healed=True, confidence=confidence,
        element_id=element_id, selector=getattr(element, "selector", None),
        message=f'Repaired: now using "{element.describe()}" ({confidence:.0%} confident). {reason}'.strip(),
    )
