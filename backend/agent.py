"""The autonomous QA agent loop.

The loop lives on the server rather than in the browser. That gives it a hard
step ceiling, a real cancel signal, per-step persistence, and one place where
assertions decide whether a run passed or failed. The frontend only renders the
event stream it receives.
"""

import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

import authoring
import storage
import visual
from config import MAX_AGENT_STEPS
from drivers import ActionResult, Snapshot, UITarget
from llm import ProviderError, Turn
from llm import registry as providers

SYSTEM_PROMPT = """You are QAi, an expert QA automation agent.

{target_line}
Each turn you receive the CURRENT SCREEN as an element tree and (when available)
a screenshot of that same screen.

EVERY reply must contain exactly one fenced ```json action block. A reply that
only describes what you intend to do does nothing — the block is what runs.
Write one short sentence, then the block.

AVAILABLE ACTIONS
```json
{"type":"action","action":"click","elementId":"el_12","reason":"why"}
{"type":"action","action":"type","elementId":"el_4","value":"text","reason":"why"}
{"type":"action","action":"clear","elementId":"el_4","reason":"why"}
{"type":"action","action":"scroll","value":"down","reason":"why"}
{"type":"action","action":"scroll","value":"down","elementId":"el_7","reason":"why"}
{"type":"action","action":"swipe","value":"left","reason":"why"}
{"type":"action","action":"key","value":"back","reason":"why"}
{"type":"action","action":"wait","value":"2","reason":"why"}
{"type":"action","action":"navigate","value":"/tr-tr/flights","reason":"why"}
{"type":"action","action":"assert_visible","elementId":"el_9","reason":"what this proves"}
{"type":"action","action":"assert_text","value":"Welcome back","reason":"what this proves"}
{"type":"action","action":"assert_absent","value":"Çerez","reason":"what this proves"}
{"type":"action","action":"assert_disabled","elementId":"el_7","reason":"what this proves"}
{"type":"action","action":"assert_visual","value":"checkout-page","reason":"what this proves"}
{"type":"action","action":"assert_no_errors","reason":"what this proves"}
{"type":"action","action":"write_scenarios","value":"Test Set name","brief":"what to test","reason":"why"}
{"type":"action","action":"run_test_set","value":"Test Set name","execution":"Execution name","reason":"why"}
{"type":"action","action":"done","value":"pass","reason":"one-line summary of the outcome"}
```

`scroll` and `swipe` take one of: up, down, left, right. Add `elementId` to
  scroll within one specific container — a dropdown, a picker wheel, a
  sub-list — instead of the whole screen. Use it whenever that container is
  smaller than the screen: a full-screen swipe can miss it entirely or scroll
  the screen behind it instead, leaving the container looking unchanged.
`key` takes one of: {keys}.
`wait` takes a number of seconds (max 10).
`navigate` goes to an address, the way a tester types one. A path like
  "/tr-tr/flights" is relative to the site already open. The run already starts
  on the page under test, so this is for reaching a *second* page or going back
  — not for opening the first one.
`assert_visual` compares the screen against a stored baseline named by `value`.
  The first time a name is used the current screen becomes the baseline and the
  check passes, so use a stable, descriptive name.
`assert_absent` proves something is NOT on the screen — a banner dismissed, a
  warning cleared, a row removed. This is how a negative outcome is proved:
  every other assert checks that something is there. It refuses to pass on a
  blank screen, so it cannot be satisfied by a page that simply had not loaded.
`assert_disabled` proves a control is on the screen but cannot be used — the
  increase button at its maximum, a submit greyed out until the form is valid.
  This is how a boundary is proved at the limit.
`assert_no_errors` fails if the page has logged a console error or a failed
  request since the run began. Use it when the goal mentions errors, or as a
  final check that the flow was clean.
`write_scenarios` writes test scenarios for the CURRENT screen into a Test Set,
  in the company standard, with a priority each. `value` is the Test Set name;
  it is created if it does not exist. `brief` is what to focus the scenarios on.
  Use write_scenarios when asked to produce, write or extract scenarios — this
  is the only way to do that, and it does not touch the screen.
`run_test_set` creates an execution for a Test Set and runs it. `value` is the
  Test Set name; `execution` is what to name the execution, omitted if none was
  given (one is generated).

When the goal contains lines shaped like `Test Set: "X"`, `Execution: "Y"` or
`Açıklama: ...` — the guided fields a tester filled in on purpose — copy each
verbatim into the matching field (Test Set → `value`, Execution → `execution`,
Açıklama → `brief`) rather than paraphrasing or re-deriving it from the rest of
the sentence. A missing line means the tester left that field blank on
purpose: leave `value` empty (the backend names the set after the screen) or
omit `execution` (one is generated) rather than inventing a name yourself.
Only fall back to reading the goal as free prose when it carries none of these
labels at all.
`done` takes "pass" or "fail" and ends the run.

A request can mix these with ordinary actions — "write the scenarios for this
screen and then run them" is write_scenarios, then run_test_set, then done. Do
not try to test the screen yourself when what was asked for is scenarios.

RULES
- Only use an elementId that appears in the CURRENT SCREEN tree. Never invent one.
- Nodes that carry an elementId are the ones you can act on. A node that is only
  a "text" entry is context — you cannot click it.
- Prefer the smallest element that carries the label you are targeting.
{target_rules}
- The backend handles waiting, visibility, scrolling into view, and coordinate
  fallbacks. Do not add your own waits before an action for those reasons.
- A test that never asserts anything is not a test. Before you finish, verify the
  outcome with assert_visible or assert_text.
- If the goal is already satisfied, emit `done` with "pass" immediately.
- If you are stuck, blocked, or the app is in an unexpected state, emit `done`
  with "fail" and explain why in `reason`. Do not loop.
- Keep prose to one or two short sentences. The action block carries the detail.
- Write your prose and your `reason` fields in the same language the GOAL is
  written in. If the goal is in Turkish, answer in Turkish. The JSON keys and
  the action names always stay exactly as written above.
- The action block must use these exact keys: "type":"action" and
  "action":"<name>". Do not put the action name in "type".
"""

TARGET_PROFILES = {
    "mobile": {
        "target_line": "You drive a real mobile device through Appium.",
        "keys": "back, home, recents, enter, search, delete",
        "target_rules": (
            "- The backend handles waiting, visibility, scrolling into view, and\n"
            "  coordinate fallbacks. Do not add your own waits for those reasons.\n"
            "- A long list gives you only the rows currently on screen: a phone\n"
            "  renders the visible ones and nothing else, so what you are looking\n"
            "  for is usually absent rather than elsewhere. If the screen has a\n"
            "  search or filter field, type into it instead of scrolling — an\n"
            "  airport picker opens on A and the airport you want may be three\n"
            "  hundred rows down. Scroll only when there is no such field.\n"
            "- Never use assert_visual here. A device screen carries a status bar\n"
            "  with a clock and a battery, so two screenshots a minute apart\n"
            "  differ whatever the app did, and the check fails on its own. Prove\n"
            "  what is on the screen with assert_text or assert_visible, and what\n"
            "  is gone — a field removed, a section no longer shown — with\n"
            "  assert_absent."
        ),
    },
    "web": {
        "target_line": "You drive a real browser page through Playwright.",
        "keys": "back, forward, enter, escape, tab, delete, search",
        "target_rules": (
            "- The backend scrolls the target into view and waits for navigation.\n"
            "  Do not add your own waits for those reasons.\n"
            "- Dismiss cookie banners and modal overlays before trying to reach the\n"
            "  content behind them; they intercept clicks.\n"
            "- Only the top of the page is in the tree. If what you need is not there,\n"
            "  scroll and read the next screen rather than guessing an elementId."
        ),
    },
}


STEPWISE_PROMPT = """

RUNNING A WRITTEN SCENARIO
This run is not open-ended: the tester has written the scenario out as numbered
steps, and you are given exactly one of them at a time. Your job for each is to
carry out that step and prove its expected result — nothing else.

{"type":"action","action":"step_done","value":"pass","reason":"what proved it"}
{"type":"action","action":"step_done","value":"fail","reason":"what was wrong"}

- Work only on the step you were given. Do not run ahead to a later one, and do
  not redo an earlier one, even if the screen makes it look convenient.
- Prove the expected result before closing the step, with assert_visible or
  assert_text. Describing what you see is not proof — a step closed as passed
  with no assertion behind it is recorded as failed, however right you were.
- Assert THIS step's expected result, and assert the most specific thing that
  proves it. A step whose expected result is "the cookie banner is dismissed"
  is proved by the banner's own text being gone, not by some other control
  being visible; a step that sets a field is proved by the value in that field.
  Re-proving what an earlier step already established says nothing new.
- ONE assertion is enough when it proves the expected result. Do not check the
  same fact two or three ways: a scenario carries dozens of steps and an
  end-to-end run carries hundreds, each costing a model call and a screenshot,
  so a redundant check is paid for on every run of that scenario for ever.
- This holds for a step you find already satisfied on arrival, which happens
  whenever the previous step's work covered it: a cookie banner dismissed
  earlier, a page already on screen. Assert what that step names, then close it.
  Never close a step without acting at all.
- Close every step with `step_done`. "pass" means the expected result held;
  "fail" means it did not, and the reason must say what you saw instead.
- A step that cannot be carried out at all is a `step_done` with "fail" — not a
  guess at the next step, and not `done`.
- `done` ends the whole scenario and is only for a failure so severe that the
  remaining steps are meaningless.
"""


def build_system_prompt(kind: str, stepwise: bool = False) -> str:
    """The loop is shared, but the vocabulary is not: a browser has no Home
    button and a phone has no browser history.

    `stepwise` swaps in the rules for a scenario the tester has written out as
    numbered steps. Those runs are judged step by step, so the model has to be
    told to stay inside the step it was handed rather than pursuing the goal
    however it sees fit.

    Substitution is done with replace(), not format(): the prompt is full of
    JSON braces that str.format would try to interpret as fields.
    """
    profile = TARGET_PROFILES.get(kind, TARGET_PROFILES["mobile"])
    prompt = SYSTEM_PROMPT + (STEPWISE_PROMPT if stepwise else "")
    for name, value in profile.items():
        prompt = prompt.replace("{" + name + "}", value)
    return prompt


ACTION_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
TERMINAL_ACTIONS = {"done", "finish", "complete"}
ASSERTION_ACTIONS = {
    "assert_visible", "assert_text", "assert_visual", "assert_no_errors",
    # Every verb above proves something is THERE. A negative or boundary
    # scenario is about what is not: a banner dismissed, a control refused at
    # its limit, a page that must not navigate. With nothing to prove those
    # with, the step closed bare and the no-assertion gate then recorded a
    # correct outcome as a failure — measured at roughly one run in five on the
    # most common such step.
    "assert_absent", "assert_disabled",
}

# Every action the agent can take. Used to recognise an action block even when
# the model puts the verb under "type" instead of "action" — a variation that
# used to make the whole block invisible, ending the run as a silent pass.
KNOWN_ACTIONS = {
    "click", "type", "clear", "scroll", "swipe", "key", "wait", "navigate",
    "assert_visible", "assert_text", "assert_visual", "assert_no_errors",
    "assert_absent", "assert_disabled",
    "write_scenarios", "run_test_set",
    "step_done",
    "done", "finish", "complete",
}

# These act on QAi rather than on the screen, so they take the snapshot as
# input instead of a target element and never count as an assertion.
AUTHORING_ACTIONS = {"write_scenarios", "run_test_set"}


@dataclass
class AgentSession:
    """Per-device-session agent state."""
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = False
    run_id: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)


_sessions: Dict[str, AgentSession] = {}


def get_session(session_id: str) -> AgentSession:
    return _sessions.setdefault(session_id, AgentSession())


def cancel(session_id: str) -> bool:
    state = _sessions.get(session_id)
    if state is None or not state.running:
        return False
    state.cancel.set()
    return True


def is_running(session_id: str) -> bool:
    state = _sessions.get(session_id)
    return bool(state and state.running)


def reset(session_id: str) -> None:
    _sessions.pop(session_id, None)


EFFORTS = ("low", "medium", "high")
DEFAULT_EFFORT = "medium"


def _resolve_effort(effort: Optional[str]) -> str:
    """Fall back rather than fail: an unknown value is not worth losing a run."""
    candidate = (effort or "").strip().lower()
    return candidate if candidate in EFFORTS else DEFAULT_EFFORT


def _resolve_provider(model: Optional[str] = None):
    """The provider, model and key the run will use. Raises if unconfigured.

    `model` overrides the Settings choice for this run only — comparing two
    models on the same scenario is the whole point, and making the user edit a
    global setting between attempts would lose that.
    """
    provider = providers.get()
    api_key = providers.api_key_for(provider.id)
    if not api_key:
        raise RuntimeError(
            f"No API key saved for {provider.label}. Add one in Settings, "
            "or switch to a provider you have configured."
        )
    return provider, (model or "").strip() or providers.active_model(), api_key


def _normalise_action(parsed: Any) -> Optional[Dict[str, Any]]:
    """Accept the schema variations models actually produce.

    The documented shape is {"type":"action","action":"click"}, but models
    routinely collapse it to {"type":"click"} or drop the wrapper entirely.
    Treating those as "no action" is dangerous rather than merely lossy: the
    run ends, and a scenario that never touched the page is reported as passed.
    """
    if not isinstance(parsed, dict):
        return None

    verb = parsed.get("action")
    if not isinstance(verb, str) or verb.lower() not in KNOWN_ACTIONS:
        # The verb may be sitting in "type" instead.
        candidate = parsed.get("type")
        if isinstance(candidate, str) and candidate.lower() in KNOWN_ACTIONS:
            parsed = {**parsed, "action": candidate}
            verb = candidate
        else:
            return None

    parsed["action"] = verb.lower()
    return parsed


def parse_action(text: str) -> Optional[Dict[str, Any]]:
    """Pull the last well-formed action block out of a model response."""
    candidates = ACTION_BLOCK.findall(text)
    if not candidates:
        # Tolerate a bare object when the model forgets the fence.
        for match in re.finditer(r'\{[^{}]*"(?:type|action)"\s*:\s*"[^"]+"[^{}]*\}', text, re.DOTALL):
            candidates.append(match.group(0))

    for raw in reversed(candidates):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        action = _normalise_action(parsed)
        if action is not None:
            return action
    return None


def looks_like_an_attempted_action(text: str) -> bool:
    """Did the model try to act, even if the block could not be parsed?

    If so, ending the run as a pass would be a lie — better to fail loudly.
    """
    if "```json" in text:
        return True
    return bool(re.search(r'"(?:type|action)"\s*:\s*"', text))


def _verdict_without_assertion(
    executed_assertion: bool, reply: str, did_authoring: bool = False,
):
    """A run that verified nothing has not passed — it just stopped.

    This is the difference between a test suite and a clicking robot, so the
    rule lives here rather than being left to the model's own judgement.

    It does not apply to a run that was asked to write scenarios or start a
    Test Set. Those never claim anything about the screen, so there is nothing
    for them to assert — and holding them to the rule marked every "write the
    scenarios for this page" as failed while it was doing exactly what it was
    asked.
    """
    if executed_assertion or did_authoring:
        return "passed", None
    return "failed", (
        "The agent stopped without asserting anything, so nothing was verified. "
        "Say what the expected outcome is and it will check for it."
    )


def _event(kind: str, **payload) -> str:
    return json.dumps({"event": kind, **payload}, ensure_ascii=False) + "\n"


# Every provider downsamples a large frame before looking at it, so the extra
# pixels of a modern phone screen are paid for twice — once uploading them each
# step, once as image tokens — and buy nothing. A 1290x2796 iPhone frame costs
# 3969 image tokens and 2.7MB on the wire; capped at this edge it is 1469 and
# under 1MB, and every control is still legible. The frame stored with the step
# is taken separately and stays full resolution, so reports do not degrade.
#
# 1024 rather than the 1568 the API tops out at: a 1440x900 desktop page fell
# *under* 1568, so it was sent untouched as a 1062KB PNG — measured at 6.7s a
# call against the gateway, where the same frame as a 103KB JPEG at this edge
# took 5.5s. 1.3s a step, for pixels that read identically.
_LLM_IMAGE_MAX_EDGE = 1024

# The live mirror is scaled to fit the panel, so a frame capped at 480px was
# being blown up several times over and looked soft. A desktop-width cap keeps a
# 1440-wide page sharp; on localhost the extra bytes are free, and the resize
# itself is cheap next to the device round trip that produced the frame.
_MIRROR_IMAGE_MAX_EDGE = 1600


def _shrink_to(screenshot: Optional[str], max_edge: int, fmt: str = "png") -> Optional[str]:
    if not screenshot:
        return screenshot
    try:
        import io

        from PIL import Image

        raw = base64.b64decode(screenshot)
        image = Image.open(io.BytesIO(raw))
        if fmt == "png" and max(image.size) <= max_edge:
            return screenshot
        if max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge))
        buffer = io.BytesIO()
        if fmt == "jpeg":
            # JPEG is what the live mirror wants: a fraction of the encode cost
            # of an optimized PNG, and small on the wire.
            image.convert("RGB").save(buffer, format="JPEG", quality=72)
        else:
            image.save(buffer, format="PNG", optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        # A frame the model (or the mirror) can still read beats no frame at
        # all: on any decoding trouble, send what the device gave us.
        return screenshot


def _shrink_for_llm(screenshot: Optional[str]) -> Optional[str]:
    # JPEG, not PNG: a screenshot PNG is six times the bytes for pixels the
    # model reads the same way, and the whole payload crosses the gateway on
    # every single step.
    return _shrink_to(screenshot, _LLM_IMAGE_MAX_EDGE, fmt="jpeg")


def _shrink_for_mirror(screenshot: Optional[str]) -> Optional[str]:
    return _shrink_to(screenshot, _MIRROR_IMAGE_MAX_EDGE, fmt="jpeg")


# A frame this uniform is a page mid-navigation — white, or the flat colour of
# a splash. Steps used to store these, and a report full of blank rectangles is
# worse than one with gaps: it looks like evidence and shows nothing.
_BLANK_SPREAD = 10


def _frame_digest(screenshot: Optional[str]) -> Optional[bytes]:
    """A thumbnail of the frame, for telling two steps' pictures apart.

    Compared rather than stored: a step whose screen is the one the last step
    already showed adds a second copy of the same picture to the report and to
    the database. On a scenario of nine steps that is tolerable; on the
    end-to-end runs this is built for, which carry hundreds, it is most of the
    storage and most of what a reader has to scroll past.
    """
    if not screenshot:
        return None
    try:
        import io as _io

        from PIL import Image

        image = Image.open(_io.BytesIO(base64.b64decode(screenshot))).convert("L")
        image.thumbnail((32, 32))
        return image.tobytes()
    except Exception:
        return None


def _frames_match(a: Optional[bytes], b: Optional[bytes], tolerance: int = 4) -> bool:
    """Do these two thumbnails show the same screen?

    A tolerance rather than equality: two JPEG captures of a page that has not
    changed still differ by a level or two, and a caret blinking somewhere is
    not a different screen.
    """
    if a is None or b is None or len(a) != len(b):
        return False
    differing = sum(1 for x, y in zip(a, b) if abs(x - y) > tolerance)
    return differing <= 0.01 * len(a)


def _is_blank_frame(screenshot: Optional[str]) -> bool:
    """Is this frame a single flat colour — a page that had not painted yet?

    Judged on a thumbnail, so it costs about a millisecond: the question is
    whether the whole frame is one tone, and that survives downsampling.
    """
    if not screenshot:
        return True
    try:
        import io

        from PIL import Image

        image = Image.open(io.BytesIO(base64.b64decode(screenshot))).convert("L")
        image.thumbnail((64, 64))
        low, high = image.getextrema()
        if (high - low) < _BLANK_SPREAD:
            return True
        # A loading page is not always perfectly uniform — a white field with a
        # spinner in the middle has a full-range extrema and is still nothing
        # worth storing. Judge it by how much of the frame is one shade.
        pixels = image.width * image.height
        return pixels > 0 and max(image.histogram()) >= 0.995 * pixels
    except Exception:
        # Unreadable is not the same as blank; keep the frame and let the
        # tester see whatever it is.
        return False


def _element_info(element: Any) -> Optional[Dict[str, Any]]:
    """A snapshot element in the shape a step record carries.

    The drivers build this inline when they act on an element; an assertion
    that only read the screen has no driver call to build it, so it is built
    here. Without bounds there is nothing to draw and nothing worth storing.
    """
    bounds = getattr(element, "bounds", None) if element is not None else None
    if not bounds:
        return None
    describe = getattr(element, "describe", None)
    return {
        "label": describe() if callable(describe) else None,
        "text": getattr(element, "text", None),
        "content-desc": getattr(element, "name", None),
        "role": getattr(element, "role", None),
        "bounds": bounds,
    }


def _step_frame(
    kind: str,
    result: Dict[str, Any],
    before_shot: Optional[str],
    after_shot: Optional[str],
    snapshot: Optional[Snapshot],
) -> Optional[str]:
    """The frame stored with one step — or None when there is nothing worth storing.

    Which frame depends on what the step did. An interaction is photographed
    *before* it runs: a click that navigates leaves nothing of the thing it
    clicked on screen, so a box drawn on the frame after it would point at a
    page the element was never on. An assertion is photographed after, because
    the screen it just verified is the evidence.

    A frame that is one flat colour is dropped rather than stored. Those are
    pages caught mid-navigation, and a report full of white rectangles looks
    like evidence while showing nothing.
    """
    if kind == "wait":
        # Nothing happened to see. The steps on either side already show it.
        return None

    frame = before_shot if before_shot is not None else after_shot
    if _is_blank_frame(frame):
        frame = after_shot if frame is before_shot else before_shot
        if _is_blank_frame(frame):
            return None

    screen = None
    if snapshot is not None:
        screen = {"width": snapshot.screen_width, "height": snapshot.screen_height}
    return _annotate_element(frame, result.get("element"), screen, ok=bool(result.get("ok")))


def _annotate_element(
    screenshot: Optional[str],
    element: Optional[Dict[str, Any]],
    screen: Optional[Dict[str, int]] = None,
    ok: bool = True,
) -> Optional[str]:
    """Outline the element this step acted on, on the step's own frame.

    A report that says `Clicked "button"` over a screenshot of a whole page
    leaves the reader hunting for which button. The box is the answer, and it
    is the same gesture for a click and for an assertion — green where the step
    proved something, amber where it did not.

    `screen` is the size the bounds were measured in. It is normally the frame's
    own size, but a shrunk frame or a device pixel ratio makes them differ, so
    the box is scaled rather than assumed to line up.
    """
    bounds = (element or {}).get("bounds") or {}
    if not screenshot or not bounds:
        return screenshot
    try:
        import io

        from PIL import Image, ImageDraw

        image = Image.open(io.BytesIO(base64.b64decode(screenshot))).convert("RGB")
        measured_w = (screen or {}).get("width") or image.width
        measured_h = (screen or {}).get("height") or image.height
        scale_x = image.width / measured_w
        scale_y = image.height / measured_h

        x1 = int(bounds.get("x1", 0) * scale_x)
        y1 = int(bounds.get("y1", 0) * scale_y)
        x2 = x1 + int(bounds.get("width", 0) * scale_x)
        y2 = y1 + int(bounds.get("height", 0) * scale_y)
        if x2 <= x1 or y2 <= y1:
            return screenshot

        # The tree carries elements up to a viewport below the fold, so a
        # target can be off-screen in this frame. Clamping one of those would
        # draw a sliver along an edge and point at the wrong thing, so a box
        # that is mostly outside is not drawn at all — the frame is still
        # worth storing, just without a claim about where to look.
        visible = (
            max(0, min(x2, image.width) - max(x1, 0))
            * max(0, min(y2, image.height) - max(y1, 0))
        )
        if visible < 0.5 * max(1, (x2 - x1) * (y2 - y1)):
            return screenshot

        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image.width - 1, x2), min(image.height - 1, y2)
        if x2 <= x1 or y2 <= y1:
            return screenshot

        colour = (34, 197, 94) if ok else (234, 179, 8)
        draw = ImageDraw.Draw(image, "RGBA")
        # A translucent wash so the box reads at a glance, then a hard edge so
        # it stays legible over a busy page.
        draw.rectangle([x1, y1, x2, y2], fill=colour + (48,))
        for width, inset in ((4, 0), (2, -3)):
            draw.rectangle(
                [x1 - inset, y1 - inset, x2 + inset, y2 + inset],
                outline=colour, width=width,
            )

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=80)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return screenshot


# The live mirror's only source while a run is in progress. An agent run
# already pulls a screenshot before and after every action; having the mirror
# poll the device on its own timer on top of that just makes both slower, so it
# reads this instead of calling target.screenshot() itself during a run.
_live_frames: Dict[str, str] = {}


def publish_live_frame(session_id: str, screenshot: Optional[str]) -> None:
    """Keep the newest frame for anyone watching without a screencast.

    Passed through rather than re-encoded when it is already a JPEG: the web
    target hands one straight out of Chromium, and putting that through Pillow
    again cost 21ms, produced a *larger* file (181KB against 176KB) and shifted
    0.8% of its pixels. A phone still sends a large PNG, which is worth
    shrinking.
    """
    if not screenshot:
        return
    already_jpeg = screenshot[:4] == "/9j/"
    _live_frames[session_id] = (
        screenshot if already_jpeg else _shrink_for_mirror(screenshot)
    )


def get_live_frame(session_id: str) -> Optional[str]:
    return _live_frames.get(session_id)


async def _fast_screenshot(target: UITarget) -> Optional[str]:
    """A frame for the model and the step record, as cheaply as the target allows.

    A run captures two of these a step, and on the web a full-page PNG measured
    266ms and 1062KB against 61ms and 176KB for the same frame as JPEG — about
    400ms a step for a report image nobody reads pixel by pixel. Visual
    baselines are the one thing that does, and they keep `screenshot()`: JPEG's
    artifacts alone differ on 2.2% of pixels, eleven times the tolerance.

    A phone has no cheaper path and falls through to its own screenshot.
    """
    fast = getattr(target, "mirror_frame", None)
    if fast is not None:
        shot = await fast()
        if shot:
            return shot
    return await target.screenshot()


async def _screen_context(target: UITarget, use_vision: bool = True):
    """Snapshot the target and return (snapshot, llm json, screenshot base64).

    Fetched one after another, not concurrently: a real device's automation
    driver (WDA on iOS in particular) serialises commands against the same
    session anyway, so two requests in flight just queue behind each other
    with extra overhead on top — measured slower end-to-end than sequential.
    When vision is off the screenshot is never shown to the model, so it is
    not fetched at all: capturing it just to throw it away was a full device
    round trip for nothing on every single step.
    """
    snapshot = await target.snapshot()
    if use_vision:
        screenshot = await asyncio.to_thread(_shrink_for_llm, await _fast_screenshot(target))
    else:
        screenshot = None
    if snapshot is None:
        return None, '{"error": "Could not read the current screen."}', screenshot
    tree = snapshot.get_optimized_tree_for_llm()
    # Compact separators, no indentation: on a large page the pretty-printing
    # alone cost more tokens than the element data it was formatting.
    return snapshot, json.dumps(tree, ensure_ascii=False, separators=(",", ":")), screenshot


def _build_turns(
    history: List[Dict[str, Any]],
    screen_json: str,
    screenshot: Optional[str],
    goal: str,
) -> List[Turn]:
    """Assemble the conversation in the provider-neutral shape.

    Only the newest screen is attached in full; older turns keep just the text.
    Re-sending every historical tree and frame is what made token cost grow
    quadratically with the number of steps.
    """
    turns = [Turn(role=entry["role"], text=entry["content"]) for entry in history]

    text = f"GOAL: {goal}\n\nCURRENT SCREEN (element tree):\n```json\n{screen_json}\n```"
    if screenshot:
        # The tree misses custom-drawn UI, icons and WebView content; pixels don't.
        text += "\n\nA screenshot of the same screen is attached. Use it when the tree is ambiguous."

    turns.append(Turn(role="user", text=text, image_b64=screenshot))
    return turns


async def _run_authoring_action(
    kind: str,
    action: Dict[str, Any],
    target: UITarget,
    snapshot: Optional[Snapshot],
    screenshot: Optional[str],
    goal: str,
) -> Dict[str, Any]:
    """Write scenarios, or run a Test Set, from inside a chat run.

    The tester asked for these in the same sentence as the testing, so they are
    reachable from the same place rather than sending the person off to another
    screen to finish the thought.
    """
    name = (action.get("value") or "").strip()
    info = target.describe() if hasattr(target, "describe") else {}

    if kind == "write_scenarios":
        # A focused brief from a guided field beats the whole chat sentence —
        # that sentence usually also carries the Test Set/execution names and
        # instructions the model has no business feeding back in as "what to
        # test". Only fall back to it when the model gave nothing more specific.
        brief = (action.get("brief") or "").strip() or goal
        outcome = await authoring.write_scenarios(
            # Passed through as-is, blank included: authoring.write_scenarios
            # already derives a name from the app/screen when this is empty —
            # inventing "Chat Test Set" here would only pre-empt that and land
            # every unnamed run under the same unhelpful label again.
            name=name,
            tree=snapshot.get_optimized_tree_for_llm() if snapshot else None,
            screenshot=screenshot,
            kind=target.kind,
            url=info.get("appId") or info.get("name"),
            brief=brief,
            # The tester's own sentence, for naming the set: "tek yön uçuş ara
            # ekranı" and "uçuş ara ekranı" are two sets, and only their words
            # tell them apart — the model's rephrased brief would not.
            asked=goal,
        )
    else:
        execution_name = (action.get("execution") or "").strip() or None
        outcome = await authoring.run_test_set(name=name, execution_name=execution_name)

    result: Dict[str, Any] = {"ok": outcome["ok"], "message": outcome["message"], "element": None}
    if outcome.get("proposed"):
        # Carried through so the loop can hand the scenarios to the tester for
        # review, rather than have them saved here sight unseen.
        result["proposed"] = {
            "scenarios": outcome["scenarios"],
            "suggestedName": outcome.get("suggestedName") or "",
            "readFrom": outcome.get("readFrom"),
            # Not "kind": that is _event()'s own first parameter, and spreading
            # this dict into it would pass the name twice.
            "targetKind": outcome.get("kind") or target.kind,
        }
    return result


async def _execute_action(
    target: UITarget,
    action: Dict[str, Any],
    snapshot: Optional[Snapshot],
) -> Dict[str, Any]:
    """Run one action against whatever target this run is driving.

    Only the platform-neutral actions live here; anything that touches a device
    or a page is delegated to the driver.
    """
    kind = (action.get("action") or "").lower()
    element_id = action.get("elementId")
    selector = action.get("xpath") or action.get("selector")
    value = action.get("value")
    snapshot_id = snapshot.snapshot_id if snapshot else None

    def as_dict(result: ActionResult) -> Dict[str, Any]:
        return {"ok": result.ok, "message": result.message, "element": result.element}

    if kind == "navigate":
        navigate = getattr(target, "navigate", None)
        if navigate is None:
            return {
                "ok": False,
                "element": None,
                "message": "This target has no address to go to — navigate is for a browser.",
            }
        return as_dict(await navigate(value or ""))

    if kind == "wait":
        seconds = min(float(value or 1), 10.0)
        await asyncio.sleep(seconds)
        return {"ok": True, "message": f"Waited {seconds:g}s", "element": None}

    if kind in ("scroll", "swipe"):
        return as_dict(await target.scroll((value or "down").lower(), element_id))

    if kind == "key":
        key = (value or action.get("key") or "back").lower()
        return as_dict(await target.press_key(key))

    if kind == "assert_visual":
        # Refused rather than discouraged on a phone. The status bar alone —
        # a clock, a battery, a signal strength — moves between two runs of the
        # same scenario, so the comparison reports a difference the app did not
        # make. It failed a step whose screen was correct, and the run was then
        # recorded as a defect that nobody could reproduce by hand.
        if (target.describe() or {}).get("kind") == "mobile":
            return {
                "ok": False,
                "message": (
                    "assert_visual is not available on a device: the status bar "
                    "clock changes between runs, so the comparison fails on its "
                    "own. Prove this with assert_text, assert_visible or "
                    "assert_absent instead."
                ),
                "element": None,
            }
        name = (value or "").strip()
        if not name:
            return {"ok": False, "message": "assert_visual needs a baseline name", "element": None}
        shot = await target.screenshot()
        if not shot:
            return {"ok": False, "message": "Could not read the screen to compare", "element": None}
        try:
            result = visual.compare(name, base64.b64decode(shot))
        except Exception as exc:
            return {"ok": False, "message": f"Visual check failed: {exc}", "element": None}
        return {
            "ok": result["passed"],
            "message": f'Visual "{name}": {result["message"]}',
            "element": None,
        }

    if kind == "assert_no_errors":
        # Peeks rather than drains: the run's own record still needs these
        # events when it closes, and draining here would swallow them.
        events = target.peek_events() if hasattr(target, "peek_events") else []
        errors = [
            e for e in events
            if e.get("level") == "error" and not e.get("thirdParty")
        ]
        if not errors:
            return {"ok": True, "message": "No console or network errors on this page", "element": None}
        first = errors[0]
        location = f" ({first['url']})" if first.get("url") else ""
        return {
            "ok": False,
            "message": (
                f"{len(errors)} page error(s). First: {first.get('kind')}: "
                f"{(first.get('text') or '')[:200]}{location}"
            ),
            "element": None,
        }

    if kind == "assert_absent":
        needle = (value or "").strip()
        if not needle:
            return {"ok": False, "message": "assert_absent needs a value", "element": None}
        await asyncio.sleep(0.4)
        fresh = await target.snapshot()
        if fresh is None:
            return {"ok": False, "message": "Could not read the screen to assert against", "element": None}
        # A blank or half-loaded page has nothing on it, so "not there" would
        # pass for the wrong reason. Absence only counts on a screen that has
        # something on it.
        if len(fresh.visible_text()) < 3:
            return {
                "ok": False,
                "message": (
                    f'Could not prove "{needle}" is gone: the screen was still '
                    "blank when it was checked."
                ),
                "element": None,
            }
        # Hidden views counted too, and deliberately: claiming something is
        # gone is a stronger claim than claiming it is there, so it has to
        # survive the wider search. A phrase the driver merely marked
        # invisible has not left the screen.
        if not fresh.contains_text(needle, include_hidden=True):
            return {"ok": True, "message": f'"{needle}" is no longer on screen', "element": None}
        return {
            "ok": False,
            "message": f'Expected "{needle}" to be gone but it is still on screen.',
            "element": _element_info(
                fresh.locate_text(needle) if hasattr(fresh, "locate_text") else None
            ),
        }

    if kind == "assert_disabled":
        if not element_id:
            return {"ok": False, "message": "assert_disabled needs an elementId", "element": None}
        await asyncio.sleep(0.3)
        fresh = await target.snapshot()
        element = fresh.elements_by_id.get(element_id) if fresh else None
        if element is None:
            return {
                "ok": False,
                "message": f"{element_id} is not on the screen any more, so it cannot be checked.",
                "element": None,
            }
        info = _element_info(element)
        label = (info or {}).get("label") or element_id
        if not getattr(element, "enabled", True):
            return {"ok": True, "message": f'"{label}" is disabled', "element": info}
        return {"ok": False, "message": f'"{label}" is still enabled', "element": info}

    if kind == "assert_text":
        needle = (value or "").strip()
        if not needle:
            return {"ok": False, "message": "assert_text needs a value", "element": None}
        # Re-read so the assertion sees the settled state, not the pre-action one.
        await asyncio.sleep(0.4)
        fresh = await target.snapshot()
        if fresh is None:
            return {"ok": False, "message": "Could not read the screen to assert against", "element": None}
        # Three answers, not two. iOS decides `visible` by hit-testing, so a
        # word under a keyboard accessory or a sheet mid-dismissal comes back
        # hidden while the run's own screenshot shows it plainly — which is how
        # an assertion for "ECONOMY" failed against a screen with ECONOMY on it.
        # Both snapshot types answer this; the page's version simply has no
        # third answer to give. The hasattr check that used to stand here hid
        # the fact that the two had drifted apart, and the same drift in
        # contains_text crashed every web run that reached an assert_absent.
        where = fresh.find_text(needle)
        if where:
            # Carried back so the step's frame can box what was verified. The
            # locate is best-effort — a phrase split across siblings has no one
            # element — and a miss costs the box, never the verdict.
            found = f'Found "{needle}" on screen'
            if where == "hidden":
                found += " (the driver reported it as not visible; matched on its position)"
            return {
                "ok": True,
                "message": found,
                "element": _element_info(
                    fresh.locate_text(needle) if hasattr(fresh, "locate_text") else None
                ),
            }
        # The whole screen, not the first dozen strings of it. Truncated, this
        # read as though the screen held nothing else — it sent the reader, and
        # me, looking for the wrong fault twice.
        strings = fresh.visible_text()
        visible = ", ".join(strings[:40])
        if len(strings) > 40:
            visible += f" … and {len(strings) - 40} more"
        return {
            "ok": False,
            "message": f'Expected "{needle}" on screen but it is not there. Visible text: {visible}',
            "element": None,
        }

    return as_dict(await target.act(kind, element_id, selector, value, snapshot_id))


async def run_agent(
    target: UITarget,
    goal: str,
    max_steps: Optional[int] = None,
    use_vision: bool = True,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    session_state: Optional["AgentSession"] = None,
    steps: Optional[List[Dict[str, str]]] = None,
) -> AsyncGenerator[str, None]:
    """Drive `target` toward `goal`, yielding NDJSON events as it goes.

    The loop is identical for a phone and for a browser page — only the driver
    behind `target` differs.

    `steps` turns the run from open-ended into a written scenario: the agent is
    handed one step at a time and has to prove that step's expected result
    before moving on. That is what makes the report answer "did step 3 pass"
    rather than only "did the run pass" — the question a tester with a written
    scenario is actually asking.

    `session_state` is an escape hatch for a run started BY another run — the
    `run_test_set` action lets a chat turn kick off a whole Test Set on the same
    device it is already driving. Left at its default, that inner call would
    share `_sessions[session_id]` with the outer one: the reentrancy guard below
    would refuse it outright (every case would "fail" in milliseconds with no
    run ever created — this is exactly the bug that shipped), and even past
    that, both calls would scribble over the same `history`/`run_id`/`cancel`.
    A suite runner driving a device from inside an agent run passes its own
    throwaway `AgentSession()` here instead, so the nested runs are invisible to
    the one that spawned them.
    """
    session_id = target.session_id
    state = session_state if session_state is not None else get_session(session_id)
    if state.running:
        yield _event("error", message="An agent run is already in progress for this session.")
        return

    # A written scenario needs room for every step, so the ceiling is scaled to
    # the number of steps rather than capped at the free-roaming limit — 20
    # steps cannot possibly fit in the 40 actions an open-ended run gets.
    planned_steps = storage.clean_steps(steps) if steps else []
    if planned_steps:
        ceiling = max_steps or (len(planned_steps) * 12 + 10)
    else:
        ceiling = min(max_steps or MAX_AGENT_STEPS, MAX_AGENT_STEPS)

    effort = _resolve_effort(effort)

    try:
        provider, model, api_key = _resolve_provider(model)
    except (RuntimeError, ProviderError) as exc:
        yield _event("error", message=str(exc))
        return

    info = target.describe()
    # Every model call this run makes adds its token counts here, and the total
    # is stored when the run ends — including a run that failed part way.
    run_usage: Dict[str, Any] = {}
    scenario_steps = planned_steps
    stepwise = bool(scenario_steps)
    system_prompt = build_system_prompt(target.kind, stepwise=stepwise)

    # Anything raised outside the loop's try block escapes the generator and
    # kills the HTTP stream, which the browser can only report as a network
    # error with no message. Every failure has to leave as an event instead.
    try:
        run_id = storage.create_run(
            goal=goal,
            platform=info.get("platform"),
            device_name=info.get("name"),
            device_udid=info.get("udid"),
            app_id=info.get("appId"),
            # Recorded as "provider/model" so an old run's report says which
            # engine produced it, even after you switch providers.
            model=f"{provider.id}/{model}",
        )
    except Exception as exc:
        yield _event("error", message=f"Could not start the run: {exc}")
        return

    state.running = True
    state.run_id = run_id
    state.cancel.clear()
    state.history = []

    yield _event("run_started", runId=run_id, goal=goal, maxSteps=ceiling)

    final_status = "passed"
    final_error: Optional[str] = None
    step_no = 0
    executed_assertion = False
    # Whether this run's job was authoring rather than testing — writing
    # scenarios, or starting a Test Set. Such a run asserts nothing about the
    # screen by design.
    did_authoring = False
    # The picture the last stored step showed, so the next one is only filed if
    # it shows something different.
    last_shot_digest: Optional[bytes] = None

    # Written-scenario bookkeeping. `step_index` is which scenario step is open,
    # `scenario_row_id` its database row, and `step_actions` how many agent actions
    # it has spent — a step that never closes itself has to be cut off rather
    # than eating the whole run's budget.
    step_index = 0
    scenario_row_id: Optional[int] = None
    # Whether the step `scenario_row_id` points at has already been resolved.
    # Without it the teardown would close a step the loop had just closed.
    step_closed = False
    step_actions = 0
    step_asserted = False
    step_started = time.monotonic()
    failed_steps = 0
    # A step that never closes itself would otherwise spend the whole run's
    # budget and starve every step after it.
    ACTIONS_PER_STEP = 12

    # A scenario that has passed before does not need the model to work out how
    # to do it again. `promote_recording` keeps what a green run did on the
    # scenario itself, and these are those actions, waiting to be carried out in
    # place of asking. The queue is emptied the moment one of them misses: past
    # that point the screen is not in the state the rest was recorded against,
    # so the model takes the step back — and the run that results, if it is
    # green, replaces the recording.
    replay_queue: List[Dict[str, Any]] = []
    replayed_actions = 0

    def open_step(index: int):
        nonlocal step_closed, replay_queue, replayed_actions
        entry = scenario_steps[index]
        step_closed = False
        replay_queue = [dict(item) for item in (entry.get("recorded") or [])]
        replayed_actions = 0
        return storage.start_scenario_step(
            run_id, index + 1, entry["action"], entry.get("expected") or None,
        )

    if stepwise:
        scenario_row_id = open_step(0)
        yield _event(
            "scenario_step_started", index=1, total=len(scenario_steps),
            action=scenario_steps[0]["action"],
            expected=scenario_steps[0].get("expected") or None,
        )

    try:
        while step_no < ceiling:
            if state.cancel.is_set():
                final_status = "cancelled"
                yield _event("cancelled", message="Run stopped by the user.")
                break

            step_no += 1
            started = time.monotonic()

            # A step that has spent its budget without closing itself is cut
            # off here and marked failed, so the steps after it still get to
            # run instead of inheriting an exhausted ceiling.
            # Replayed actions do not count against it. The budget is there to
            # stop the model grinding at a step it cannot do; a recording that
            # turned out to be stale should hand over the full budget rather
            # than a step already half spent. The report still shows the total.
            if stepwise and (step_actions - replayed_actions) >= ACTIONS_PER_STEP:
                stalled = (
                    f"Step {step_index + 1} used {ACTIONS_PER_STEP} actions without "
                    "reaching its expected result."
                )
                storage.finish_scenario_step(
                    scenario_row_id, "failed", message=stalled,
                    actions_used=step_actions,
                    duration_ms=int((time.monotonic() - step_started) * 1000),
                )
                step_closed = True
                yield _event(
                    "scenario_step_finished", index=step_index + 1,
                    total=len(scenario_steps), status="failed", message=stalled,
                )
                failed_steps += 1
                if final_error is None:
                    final_error = stalled
                step_index += 1
                if step_index >= len(scenario_steps):
                    final_status = "failed"
                    yield _event("finished", status="failed", summary=final_error)
                    break
                scenario_row_id = open_step(step_index)
                step_actions = 0
                step_asserted = False
                step_started = time.monotonic()
                state.history = state.history[-4:]
                yield _event(
                    "scenario_step_started", index=step_index + 1,
                    total=len(scenario_steps),
                    action=scenario_steps[step_index]["action"],
                    expected=scenario_steps[step_index].get("expected") or None,
                )
                continue

            snapshot, screen_json, screenshot = await _screen_context(target, use_vision)
            publish_live_frame(target.session_id, screenshot)
            if snapshot is not None:
                yield _event("snapshot", snapshotId=snapshot.snapshot_id, step=step_no)

            # In a written scenario the model is shown the step it is on, not
            # the scenario as a whole — handing it the finished article invites
            # it to skip ahead to whichever step the current screen suits.
            if stepwise:
                current = scenario_steps[step_index]
                focus = (
                    f"STEP {step_index + 1} OF {len(scenario_steps)}: {current['action']}"
                )
                if current.get("expected"):
                    focus += f"\nEXPECTED RESULT: {current['expected']}"
                focus += (
                    f"\n\n(Scenario: {goal})" if goal else ""
                )
            else:
                focus = goal

            # The recording, when there is one, is taken instead of asking —
            # not as a shortcut through the loop but as the same kind of thing
            # the model would have produced, so it is executed, recorded and
            # reported by exactly the code below that handles a model action.
            action: Optional[Dict[str, Any]] = None
            replaying = False
            # Only the model path produces one, but the no-action handling
            # below reads it either way.
            reply = ""
            if replay_queue:
                entry = replay_queue.pop(0)
                action = {
                    "action": entry.get("action"),
                    "selector": entry.get("selector"),
                    "value": entry.get("value"),
                    "reason": "replayed from the last green run",
                }
                replaying = True
                replayed_actions += 1
                yield _event("replaying", step=step_no, action=action["action"],
                             label=entry.get("label"))

            # When the recording runs out having proved the step's expected
            # result, the step is closed on that proof alone. Without an
            # assertion behind it the model still takes over — a recording that
            # verified nothing is a sequence of clicks, not a passed step.
            elif stepwise and replayed_actions and step_asserted and not step_closed:
                action = {
                    "action": "step_done", "value": "pass",
                    "reason": "carried out from the last green run's recording",
                }

            if action is None:
                turns = _build_turns(
                    state.history, screen_json, screenshot if use_vision else None, focus,
                )

                yield _event("thinking", step=step_no)
                reply = ""
                try:
                    async for token in provider.stream(
                        system_prompt, turns, model, api_key, effort, usage=run_usage,
                    ):
                        reply += token
                        yield _event("token", text=token, step=step_no)
                except ProviderError as exc:
                    final_status, final_error = "failed", str(exc)
                    yield _event("error", message=final_error)
                    break
                except Exception as exc:
                    final_status, final_error = "failed", f"{provider.label} error: {exc}"
                    yield _event("error", message=final_error)
                    break

                # An empty response is its own failure, not "the model is done".
                # Reporting it as "finished without asserting" hides the cause.
                if not reply.strip():
                    final_status = "failed"
                    final_error = (
                        f"{provider.label} returned an empty response for step {step_no}. "
                        "This is usually a safety filter rejecting the screenshot or the "
                        "scenario text. Try turning Vision off, rewording the goal, or "
                        "switching provider in Settings."
                    )
                    yield _event("error", message=final_error)
                    break

                state.history.append({"role": "assistant", "content": reply})

                action = parse_action(reply)

                # Models regularly describe the action instead of emitting it
                # ("I will enter an invalid email."). One corrective nudge recovers
                # the step; giving up here would end a perfectly good run on the
                # first turn.
                if action is None and not looks_like_an_attempted_action(reply):
                    state.history.append({
                        "role": "user",
                        "content": (
                            "That reply had no action block, so nothing ran. Reply again with "
                            "exactly one fenced ```json block using the documented keys "
                            '("type":"action" and "action":"<name>"). If the goal is already '
                            'met, use the `done` action.'
                        ),
                    })
                    retry_turns = _build_turns(
                        state.history, screen_json, screenshot if use_vision else None, goal
                    )
                    retry = ""
                    try:
                        async for token in provider.stream(
                            system_prompt, retry_turns, model, api_key, effort, usage=run_usage,
                        ):
                            retry += token
                            yield _event("token", text=token, step=step_no)
                    except (ProviderError, Exception) as exc:
                        final_status, final_error = "failed", str(exc)
                        yield _event("error", message=final_error)
                        break

                    state.history.append({"role": "assistant", "content": retry})
                    action = parse_action(retry)
                    if action is not None:
                        reply = retry

            if action is None:
                # The tokens are already on screen; re-emitting them as a
                # message would print the same paragraph twice.
                if looks_like_an_attempted_action(reply):
                    # The model tried to act and we could not read it. Ending
                    # as a pass here is how a run that touched nothing gets
                    # reported green.
                    final_status = "failed"
                    final_error = (
                        "The model emitted an action block QAi could not parse, so no step ran. "
                        "Raw reply: " + reply.strip()[:300]
                    )
                    yield _event("error", message=final_error)
                    break

                final_status, final_error = _verdict_without_assertion(
                    executed_assertion, reply.strip(), did_authoring,
                )
                yield _event("finished", status=final_status, summary=(final_error or reply.strip())[:400])
                break

            kind = (action.get("action") or "").lower()
            reason = action.get("reason") or ""

            if kind == "step_done" and stepwise:
                verdict = (action.get("value") or "pass").lower()
                passed = verdict.startswith("pass")
                # A step that claims to pass while proving nothing is the same
                # trap as a green run with no assertion, one scale down.
                if passed and not step_asserted and scenario_steps[step_index].get("expected"):
                    passed = False
                    reason = (
                        (reason + " — ") if reason else ""
                    ) + "closed as passed without verifying the expected result."

                storage.finish_scenario_step(
                    scenario_row_id, "passed" if passed else "failed",
                    message=reason or None, actions_used=step_actions,
                    duration_ms=int((time.monotonic() - step_started) * 1000),
                )
                step_closed = True
                yield _event(
                    "scenario_step_finished", index=step_index + 1,
                    total=len(scenario_steps),
                    status="passed" if passed else "failed", message=reason,
                )
                if not passed:
                    failed_steps += 1
                    if final_error is None:
                        final_error = f"Step {step_index + 1} failed: {reason or 'no reason given'}"

                step_index += 1
                if step_index >= len(scenario_steps):
                    final_status = "failed" if failed_steps else "passed"
                    if failed_steps:
                        final_error = (
                            f"{failed_steps} of {len(scenario_steps)} steps failed. "
                            + (final_error or "")
                        ).strip()
                    summary = (
                        f"All {len(scenario_steps)} steps passed."
                        if not failed_steps else final_error
                    )
                    yield _event("finished", status=final_status, summary=summary)
                    break

                scenario_row_id = open_step(step_index)
                step_actions = 0
                step_asserted = False
                step_started = time.monotonic()
                # Only the newest screen is carried forward: the previous step's
                # back-and-forth is finished business, and leaving it in tempts
                # the model to re-read an instruction it has already completed.
                state.history = state.history[-4:]
                yield _event(
                    "scenario_step_started", index=step_index + 1,
                    total=len(scenario_steps),
                    action=scenario_steps[step_index]["action"],
                    expected=scenario_steps[step_index].get("expected") or None,
                )
                continue

            if kind in TERMINAL_ACTIONS:
                verdict = (action.get("value") or "pass").lower()
                final_status = "passed" if verdict.startswith("pass") else "failed"
                if final_status == "failed":
                    final_error = reason or "The agent reported the scenario as failed."
                elif not executed_assertion and not did_authoring:
                    # A green run that verified nothing is worse than a red one:
                    # it gets trusted. A run that was asked to write scenarios
                    # is exempt — it never claimed to test anything.
                    final_status = "failed"
                    final_error = (
                        "The agent finished without asserting anything, so nothing was "
                        "actually verified. Add an expected outcome to the scenario "
                        '(for example: "…and verify the results page shows flights").'
                    )
                storage.add_step(
                    run_id, action="done", status="passed" if final_status == "passed" else "failed",
                    reason=reason, message=final_error or reason,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                yield _event("finished", status=final_status, summary=final_error or reason)
                break

            yield _event(
                "step_started", step=step_no, action=kind,
                target=action.get("elementId") or action.get("xpath"),
                value=action.get("value"), reason=reason,
            )

            # An interaction is photographed before it runs, so the box drawn
            # on its frame sits over the thing it actually acted on rather than
            # over whatever the click navigated to. Assertions need no such
            # frame: the screen they verified is the one that comes after.
            before_shot = None
            if action.get("elementId") and kind not in ASSERTION_ACTIONS:
                before_shot = await _fast_screenshot(target)

            if kind in AUTHORING_ACTIONS:
                # These need the screen as material, not as a place to click,
                # so they are handled here where the snapshot is still in hand.
                did_authoring = True
                result = await _run_authoring_action(
                    kind, action, target, snapshot, screenshot, goal,
                )
            else:
                result = await _execute_action(target, action, snapshot)
            after_shot = await _fast_screenshot(target)
            publish_live_frame(target.session_id, after_shot)
            duration_ms = int((time.monotonic() - started) * 1000)

            if kind in ASSERTION_ACTIONS and result["ok"]:
                executed_assertion = True
                step_asserted = True

            # The recording has stopped describing this screen. Everything
            # still queued was recorded against a state the page is no longer
            # in, so it is dropped and the model takes the step from here —
            # with the part that did work already applied, which is the same
            # position it would be in had it done those actions itself.
            if replaying and not result["ok"]:
                replay_queue.clear()
                yield _event(
                    "replay_abandoned", step=step_no, action=kind,
                    message=result["message"],
                )

            # Stored only when it shows something the last stored frame did
            # not. Two steps in a row proving things about the same unchanged
            # screen used to file the same picture twice.
            step_shot = _step_frame(kind, result, before_shot, after_shot, snapshot)
            shot_digest = _frame_digest(step_shot)
            if step_shot is not None and _frames_match(shot_digest, last_shot_digest):
                step_shot = None
            elif shot_digest is not None:
                last_shot_digest = shot_digest

            step_actions += 1
            step_status = "passed" if result["ok"] else "failed"
            step_row_id = storage.add_step(
                run_id,
                action=kind,
                status=step_status,
                target=(result.get("element") or {}).get("label") or action.get("elementId"),
                value=action.get("value"),
                reason=reason,
                message=result["message"],
                element=result.get("element"),
                screenshot=step_shot,
                duration_ms=duration_ms,
                # How the element was addressed, taken from the action first.
                # A replayed action is given a selector rather than an
                # elementId, so the driver resolves it without a snapshot and
                # has no element to hand back — leaving this to be derived from
                # one meant a replayed action recorded no selector, and the run
                # that replayed a recording perfectly then erased it.
                selector=action.get("selector") or action.get("xpath"),
                # Which scenario step this served, so a green run's work can be
                # kept on that step and replayed instead of re-derived.
                scenario_idx=(step_index + 1) if stepwise else None,
            )

            yield _event(
                "step_finished", step=step_no, stepId=step_row_id, action=kind,
                status=step_status, message=result["message"],
                element=result.get("element"), durationMs=duration_ms,
            )
            if result.get("proposed"):
                # The scenarios go to the tester, not the database: the chat
                # opens them for review, and saving is their call.
                yield _event("scenarios_proposed", step=step_no, **result["proposed"])

            # A failed assertion ends the run; a failed interaction is reported
            # back to the model so it can try a different route.
            #
            # Not when it was replayed, though. A recorded assertion that no
            # longer holds says the recording is stale, which is exactly the
            # case the model is there for — ending the run on it would make a
            # scenario that has passed before fail for having passed before.
            if not result["ok"] and kind in ASSERTION_ACTIONS and not replaying:
                final_status, final_error = "failed", result["message"]
                yield _event("finished", status="failed", summary=result["message"])
                break

            state.history.append({
                "role": "user",
                "content": (
                    f"Step {step_no} ({kind}) {'succeeded' if result['ok'] else 'FAILED'}: {result['message']}\n"
                    "The screen below is the result. Continue toward the goal, or emit `done`."
                ),
            })

            # Keep the transcript bounded; only the recent turns matter.
            if len(state.history) > 24:
                state.history = state.history[-24:]

            # A brief settle window for whatever the action just triggered
            # (a transition, a keyboard animation) before the next snapshot.
            # Longer than this only added dead time between steps.
            await asyncio.sleep(0.3)
        else:
            final_status = "failed"
            final_error = f"Reached the {ceiling} step ceiling without finishing."
            yield _event("finished", status="failed", summary=final_error)

    except Exception as exc:
        final_status, final_error = "failed", str(exc)
        yield _event("error", message=str(exc))
    finally:
        # A scenario step is opened before its work and closed after it, so a
        # run that ends in between — cancelled, out of budget, a provider that
        # stopped answering — leaves one sitting at "running" for ever. The
        # report then shows a step that never resolved, which reads as the
        # product hanging rather than the run ending.
        if stepwise and scenario_row_id is not None and not step_closed:
            storage.finish_scenario_step(
                scenario_row_id,
                "cancelled" if final_status == "cancelled" else "failed",
                message=final_error or "The run ended before this step closed.",
                actions_used=step_actions,
                duration_ms=int((time.monotonic() - step_started) * 1000),
            )
        storage.finish_run(run_id, final_status, final_error)
        # Written here rather than per step: the total is what a run costs, and
        # a run that failed or was stopped still spent what it spent.
        storage.record_run_usage(run_id, run_usage)
        state.running = False
        state.cancel.clear()
        yield _event("run_closed", runId=run_id, status=final_status, steps=step_no)
