"""Test scenarios written to the Digital Channels standard.

Two standards govern every scenario a tester writes here: a sentence format
(`Module - Submodule | Data&Precondition - Action&Expected Result`) and a
priority rubric that asks what breaks if the scenario fails. Both live in
docs/scenario-standards.md; this module turns them into a prompt and, more
importantly, into a validator.

The validator is the point. A model asked for a format will mostly obey and
occasionally not, and a scenario that quietly breaks the format is worse than
no scenario — it lands in a Test Set and gets copied by the next person. So
nothing reaches the caller without being parsed, checked against the format and
the four allowed priorities, and either corrected or dropped with a reason.

Scope: E2E and Component only. QAi drives a UI, so an API scenario it could
never run would just be dead weight in a Test Set.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import storage
from llm import Turn
from llm import registry as providers

LAYERS = ("E2E", "Component")
PRIORITIES = ("Critical", "High", "Medium", "Low")

# "Module - Submodule | <data & precondition> - <action & expected>".
# The module half must carry a " - ", and the content half must carry its own,
# which is what separates a real scenario from a sentence with a pipe in it.
FORMAT = re.compile(r"^[^|]+\s-\s[^|]+\|\s*.+\s-\s.+$")

SYSTEM_PROMPT = """You write test scenarios for Turkish Airlines digital channels
(TK Web and TK Mobile), to a fixed company standard.

FORMAT — every scenario title is one line, entirely in English:

    Module - Submodule | Data&Precondition - Action&Expected Result

- "|" separates the definition half (Module - Submodule) from the content half.
- Inside the content half, " - " separates Data & Precondition from
  Action & Expected Result.
- Main module first, then submodule: "Booking - Availability", never the reverse.
- Modules in use: Booking, Manage Booking, Check-in, Standalone, IRROPS,
  Miles&Smiles, Booker/TKPay.

STANDARD CODES — use these, never prose equivalents:
- Route: OW (one way), RT (round trip), MC (multi city)
- Flight type: DOM, INT
- Passengers: ADT CHD INF STU DIS VET YNG YTH, count before the code: "2 ADT 1 CHD"
- Brand/cabin: EcoFly ExtraFly PrimeFly ECO BUS
- Ancillaries: XBAG PETC AVIH OBAG LNG SAF SPEQ FQTV FQTU Mile
- Payment types always carry the currency: Klarna [USD], Alipay [EURO]

SQUARE BRACKETS — a detail belonging to a datum goes immediately after it in
brackets: INT [USA], PETC [Dog], SPEQ [Golf], 1 ADT [EXIT SEAT+XBAG],
[Slow Internet Connection], [1. Segment WK].

The expected result must be an explicit control point — what is verified, not
just what is done. "control PNR and ticket number", not "complete booking".

PRIORITY — this measures technical impact: "if this scenario fails, what breaks?"
Answer four questions first: does the main flow stop or only a side flow; does it
hit all passengers or a narrow edge case; can the user finish another way; is
there data-integrity, financial, security or legal risk (if yes, at least High).

E2E:
- Critical: the main journey cannot complete (booking, payment, check-in, ticket
  issue). Financial or data loss risk.
- High: main flow completes but an important side step (seat, ancillary, extra
  baggage) is badly broken, affecting a wide audience.
- Medium: flow completes; a meaningful deviation on an informational or secondary
  screen, workable around.
- Low: discouraged at E2E. A visual finding belongs in the Component layer.

Component:
- Critical: the user cannot pass the step at all and there is no alternative screen.
- High: the component misbehaves; the step only completes by retrying or another
  route. Display that misleads the user into a wrong action.
- Medium: display or information is wrong but the transaction completes.
- Low: visual, alignment, label, icon, language/format differences.

CALIBRATION:
- A scenario's priority can never exceed the priority of the business flow it protects.
- Negative/validation scenarios sit one band below their positive counterpart.
  Exception, these do not step down: security, authorisation, price/fee, and
  data-writing scenarios.
- At E2E, Low is rare and Critical is common — E2E exists to protect main flows.
- Irrelevant to priority: whether it is new development, in the regression pack,
  automated, slow to run, or who asked for it.

HOW MANY — cover what is actually there. A screen with one form deserves a few
scenarios; a booking flow with payment, seats and ancillaries deserves more.
Do not pad to a number and do not stop short of a flow that matters. Twenty is
the ceiling.

WHEN THE REQUEST IS TOO VAGUE TO WRITE AGAINST — say so instead of guessing.
If you cannot tell which route, passenger mix, payment method or precondition
is meant, and guessing would produce scenarios the team would have to rewrite,
reply with questions instead of scenarios:

{"questions": ["<question, Turkish>", "..."]}

Ask at most three, only about things that would change the scenarios. Never ask
when a live screen is attached and the answer is visible on it, and never ask
merely to confirm something reasonable — a sensible default beats a question.

OTHERWISE — reply with one JSON array and nothing else:
[
  {"title": "<the one-line scenario, English, in the format above>",
   "layer": "E2E" | "Component",
   "priority": "Critical" | "High" | "Medium" | "Low",
   "goal": "<what the agent should actually do, plain instruction, one or two sentences>",
   "steps": [
     {"action": "<one thing to do on the screen>",
      "expected": "<what must be true afterwards>"}
   ],
   "rationale": "<why this priority — one sentence, Turkish>"}
]

`goal` is what the team reads as the summary; `steps` is what QAi executes, one
step at a time, and each step is reported pass or fail on its own.

WRITING STEPS:
- One action per step. "Fill the passenger form and continue" is two steps.
- `expected` is a control point that can be seen on the screen — "the passenger
  form opens with 2 ADT rows", not "it works". This is what the step is judged
  against, so a vague expected result produces a vague report.
- A step whose result cannot be checked on screen may leave `expected` empty; it
  is then reported as carried out rather than verified. Use this sparingly, for
  pure navigation.
- Between 2 and 12 steps. If a scenario needs more, it is two scenarios.
- Write the steps in the order they must run, starting from the screen the
  scenario begins on, and end on the check the title promises.

STANDARDISE RECURRING STEPS — this matters as much as the format. Across the
whole set you return, a step that does the same thing must be written the same
way, word for word, in both `action` and `expected`. Only the parts that genuinely
differ between scenarios change; the sentence structure does not. A reader should
be able to scan the set and see the shared preamble is identical everywhere.

Use these canonical openers verbatim whenever they apply, filling only the
bracketed parts:
- "Open the application and wait for the home screen to load."
  expected: "The home screen is displayed."
- "Close the cookie consent banner by tapping Accept."
  expected: "The cookie banner is dismissed and no longer covers the page."
- "Log in with <account>."  expected: "The account/home screen for <account> is shown."
- "From the home screen, open <module>."  expected: "The <module> screen is displayed."

Do not paraphrase these ("dismiss cookies" in one scenario and "close the cookie
popup" in another is wrong). If a scenario does not need one of them, omit it —
but when two scenarios both need it, the wording is identical. Invent a canonical
phrasing the same way for any other step that recurs across the set, and reuse it.
"""


def _screen_brief(tree: Optional[Dict[str, Any]], url: Optional[str], kind: str) -> str:
    if tree is None:
        return ""
    body = json.dumps(tree, ensure_ascii=False, separators=(",", ":"))
    where = f"\nThe page under test is {url}." if url else ""
    return (
        f"\n\nThis is the live {kind} screen the scenarios must fit. Only write "
        f"scenarios that can actually be run against what is here — real fields, "
        f"real buttons, real labels.{where}\n\nELEMENT TREE:\n```json\n{body}\n```"
    )


def build_turns(
    *,
    kind: str,
    brief: Optional[str] = None,
    tree: Optional[Dict[str, Any]] = None,
    url: Optional[str] = None,
    screenshot: Optional[str] = None,
    answers: Optional[str] = None,
) -> List[Turn]:
    """The request, in the provider-neutral shape the agent already speaks."""
    asked = (
        "Write the test scenarios this warrants."
        + (f"\n\nWhat to cover: {brief}" if brief else "")
        + _screen_brief(tree, url, kind)
        + (f"\n\nAnswers to your earlier questions:\n{answers}" if answers else "")
    )
    if screenshot:
        asked += (
            "\n\nA screenshot of the same screen is attached. Use it for anything "
            "the tree cannot show — icon-only controls, custom-drawn UI."
        )
    return [Turn(role="user", text=asked, image_b64=screenshot)]


def _strip_fence(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    bare = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    return bare.group(0) if bare else text


def _questions(raw: Any) -> List[str]:
    """The model asking rather than guessing is a useful answer, not a failure."""
    if not isinstance(raw, dict):
        return []
    asked = raw.get("questions")
    if not isinstance(asked, list):
        return []
    return [str(q).strip() for q in asked if str(q).strip()][:3]


def _clean_priority(value: Any, layer: str) -> str:
    """Snap to an allowed band, and keep E2E off Low.

    The standard calls Low at E2E "discouraged" and says the finding belongs in
    the Component layer — so a Low E2E scenario is a mis-layered one, and Medium
    is the honest floor for something that still runs a whole journey.
    """
    candidate = str(value or "").strip().title()
    if candidate not in PRIORITIES:
        candidate = "Medium"
    if layer == "E2E" and candidate == "Low":
        return "Medium"
    return candidate


def _clean_layer(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return "Component" if candidate == "component" else "E2E"


def parse(text: str) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """Return (scenarios, rejections, questions).

    Rejections are surfaced rather than swallowed: if the model wrote six
    scenarios and two broke the format, the tester should see that two were
    dropped and why, not silently receive four.
    """
    try:
        raw = json.loads(_strip_fence(text))
    except Exception:
        return [], ["The model did not return a JSON array of scenarios."], []

    asked = _questions(raw)
    if asked:
        return [], [], asked

    if isinstance(raw, dict):
        raw = raw.get("scenarios") or raw.get("items") or [raw]
    if not isinstance(raw, list):
        return [], ["The model did not return a JSON array of scenarios."], []

    scenarios: List[Dict[str, Any]] = []
    rejected: List[str] = []

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = " ".join(str(entry.get("title") or "").split())
        goal = str(entry.get("goal") or "").strip()
        if not title:
            continue
        if not FORMAT.match(title):
            rejected.append(title[:120])
            continue

        layer = _clean_layer(entry.get("layer"))
        scenarios.append({
            "title": title[:200],
            "layer": layer,
            "priority": _clean_priority(entry.get("priority"), layer),
            # A scenario with no runnable instruction still has a usable title;
            # falling back to it beats dropping the scenario entirely.
            "goal": goal or title,
            # Same cleaning the editor and the API go through, so a generated
            # scenario and a hand-written one are the same kind of thing.
            # A scenario that comes back without usable steps is still worth
            # keeping — it runs open-ended, judged as a whole, as before.
            "steps": storage.clean_steps(entry.get("steps")),
            "rationale": str(entry.get("rationale") or "").strip()[:300],
        })

    return scenarios, rejected, []


async def generate(
    *,
    kind: str,
    brief: Optional[str] = None,
    tree: Optional[Dict[str, Any]] = None,
    url: Optional[str] = None,
    screenshot: Optional[str] = None,
    answers: Optional[str] = None,
    model: Optional[str] = None,
    effort: str = "medium",
) -> Dict[str, Any]:
    provider = providers.get()
    api_key = providers.api_key_for(provider.id)
    if not api_key:
        raise RuntimeError(
            f"No API key saved for {provider.label}. Add one in Settings, "
            "or switch to a provider you have configured."
        )

    turns = build_turns(
        kind=kind, brief=brief, tree=tree, url=url,
        screenshot=screenshot, answers=answers,
    )
    chosen = (model or "").strip() or providers.active_model()

    collected = []
    async for token in provider.stream(SYSTEM_PROMPT, turns, chosen, api_key, effort):
        collected.append(token)

    scenarios, rejected, questions = parse("".join(collected))
    return {
        "scenarios": scenarios,
        "rejected": rejected,
        "questions": questions,
        "model": chosen,
        "readFrom": url if tree is not None else None,
    }
