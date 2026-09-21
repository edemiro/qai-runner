"""Turning a failed scenario into a defect someone else can act on.

The run already holds everything a bug report needs — which step failed, what
it was supposed to prove, what the agent saw instead, what the page complained
about, and a picture of the screen at the moment. Asking a tester to retype
that into a tracker is asking them to transcribe, and what actually happens is
that the failure is described from memory an hour later, or not raised at all.

So the report is composed here and handed over as a draft. Nothing is saved
until the tester has read it: a generated bug that files itself is a bug nobody
has checked, and QAi is wrong often enough — an expected string the page never
rendered, a limit the scenario invented — that half of what it would raise is
about the scenario rather than the app.
"""

import re
from typing import Any, Dict, List, Optional

import storage

# What went wrong, in a form that groups. Derived from the failures this has
# actually produced rather than invented: the counts in brackets are from the
# run history at the time these were written.
CODES: Dict[str, str] = {
    # The app did not do what the scenario said it should. The only family that
    # is a defect in the app by default.
    "EXPECTATION_NOT_MET": "The step's expected result did not hold",       # 26
    "TEXT_NOT_FOUND": "Expected text was not on the screen",                # 8
    "ELEMENT_GONE": "The element was no longer on the page",
    "ELEMENT_NOT_VISIBLE": "The element was on the page but not visible",
    # The run could not reach a verdict. Usually the scenario or the
    # environment rather than the feature, and worth separating for exactly
    # that reason — these should not be filed against a developer unreviewed.
    "STEP_BUDGET_EXHAUSTED": "The step ran out of actions before proving itself",  # 34
    "STEP_UNPROVEN": "The step was closed without proving its expected result",    # 17
    "ACTION_TIMEOUT": "An action timed out",                                # 33
    "NAVIGATION_FAILED": "The page could not be reached",
    "PAGE_ERRORS": "The page logged errors while the run was otherwise green",
    "RUN_CANCELLED": "The run was stopped before it finished",
    "UNCLASSIFIED": "The run failed without a recognised cause",
}

# Codes that describe the tooling or the scenario rather than the product. A
# reader triaging a list needs to know which half they are looking at.
NOT_APP_DEFECTS = {
    "STEP_BUDGET_EXHAUSTED", "STEP_UNPROVEN", "ACTION_TIMEOUT",
    "NAVIGATION_FAILED", "RUN_CANCELLED",
    # "I could not work out why" is not evidence of anything. It was treated as
    # a defect by default, which is how a run that passed every step — four of
    # four green, no error recorded — filed a bug against the app saying "the
    # run failed without a recognised cause". Nobody should get a defect
    # assigned to them because the tool had no idea.
    "UNCLASSIFIED",
}


def classify(run: Dict[str, Any]) -> str:
    """The most specific cause the run's own record supports.

    Read from the narrowest evidence outward: the agent step that actually
    failed says more than the scenario step around it, which says more than the
    run's summary.
    """
    for step in run.get("steps") or []:
        if step.get("status") != "failed":
            continue
        message = (step.get("message") or "").lower()
        if "no longer on the page" in message:
            return "ELEMENT_GONE"
        if "not visible" in message:
            return "ELEMENT_NOT_VISIBLE"
        if "is not there" in message or "but it is not there" in message:
            return "TEXT_NOT_FOUND"
        if "timeout" in message or "timed out" in message:
            return "ACTION_TIMEOUT"
        if "net::" in message or "err_" in message:
            return "NAVIGATION_FAILED"

    for step in run.get("scenarioSteps") or []:
        if step.get("status") != "failed":
            continue
        message = (step.get("message") or "").lower()
        if "without reaching its expected result" in message:
            return "STEP_BUDGET_EXHAUSTED"
        if "without verifying" in message:
            return "STEP_UNPROVEN"
        return "EXPECTATION_NOT_MET"

    error = (run.get("error") or "").lower()
    if "stopped by the user" in error or run.get("status") == "cancelled":
        return "RUN_CANCELLED"
    if "page error" in error:
        return "PAGE_ERRORS"
    if "net::" in error or "err_" in error:
        return "NAVIGATION_FAILED"
    if error:
        return "EXPECTATION_NOT_MET"
    return "UNCLASSIFIED"


def _failed_scenario_step(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for step in run.get("scenarioSteps") or []:
        if step.get("status") == "failed":
            return step
    return None


def _failed_agent_step(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for step in run.get("steps") or []:
        if step.get("status") == "failed":
            return step
    return None


def _summarise(text: Optional[str], limit: int = 120) -> str:
    """One line of it, with the sentence kept whole where it fits."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    boundary = max(cut.rfind(". "), cut.rfind(" — "), cut.rfind("; "))
    return (cut[:boundary] if boundary > 40 else cut).rstrip(" ,;—") + "…"


def _title(run: Dict[str, Any], code: str) -> str:
    """A title that says which scenario, which step, and what went wrong.

    Long enough to be recognised in a list without opening it — a tracker full
    of "Test failed" is a tracker nobody reads.
    """
    scenario = _summarise(run.get("title") or "Scenario", 90)
    failed = _failed_scenario_step(run)
    if failed:
        return f"{scenario} — step {failed.get('idx')}: {_summarise(failed.get('action'), 70)}"
    agent_step = _failed_agent_step(run)
    if agent_step:
        return f"{scenario} — {agent_step.get('action')}: {_summarise(agent_step.get('message'), 70)}"
    return f"{scenario} — {CODES.get(code, 'failed')}"


def _reproduction(run: Dict[str, Any]) -> List[str]:
    """The steps up to and including the one that failed.

    Everything after it is what the agent did next, which is not part of
    reproducing the defect and only makes the report longer.
    """
    lines = []
    for step in run.get("scenarioSteps") or []:
        mark = "✗" if step.get("status") == "failed" else "✓"
        lines.append(f"{mark} {step.get('idx')}. {step.get('action')}")
        if step.get("expected"):
            lines.append(f"     expected: {step['expected']}")
        if step.get("status") == "failed":
            break
    return lines


def _event_line(event: Dict[str, Any]) -> str:
    """One page event, with everything needed to look it up again.

    The whole line, not a summary of it: whoever reads this bug is going to
    paste the URL into a network tab or search the text in the source, and a
    truncated message costs them that. Console goes in as `console:` and a
    request as its status and method, because the two are looked for in
    different places.
    """
    kind = (event.get("kind") or "").lower()
    text = " ".join((event.get("text") or "").split())
    url = event.get("url") or ""
    status = event.get("status")

    if kind == "console":
        line = f"- console: {_summarise(text, 400)}"
    elif kind == "pageerror":
        line = f"- script hatası: {_summarise(text, 400)}"
    elif status:
        line = f"- network: HTTP {status}"
        if event.get("resourceType"):
            line += f" ({event['resourceType']})"
        if text and not text.startswith("HTTP"):
            line += f" — {_summarise(text, 200)}"
    else:
        line = f"- {kind or 'event'}: {_summarise(text, 400)}"

    if url:
        line += f"\n  {url}"
    return line


def _page_event_section(run: Dict[str, Any]) -> List[str]:
    """What the page complained about, errors and warnings alike.

    Warnings are here on purpose. Every 4xx is a warning — a live airline site
    answers them all through a working booking, so they must not fail a run —
    but a bug *about* a 404 used to carry no trace of it: the section only
    listed errors, and there were none. The report showed a cause and no
    evidence for it.
    """
    events = [e for e in (run.get("pageEvents") or []) if not e.get("thirdParty")]
    if not events:
        return []

    errors = [e for e in events if e.get("level") == "error"]
    warnings = [e for e in events if e.get("level") != "error"]

    body = [""]
    if errors:
        body.append(f"**Sayfa hataları** ({len(errors)})")
        body.extend(_event_line(event) for event in errors[:8])
    if warnings:
        if errors:
            body.append("")
        body.append(
            f"**Sayfa uyarıları** ({len(warnings)}) — koşumun sonucunu "
            f"belirlemez, 4xx ve konsol bildirimleri burada"
        )
        body.extend(_event_line(event) for event in warnings[:8])
        if len(warnings) > 8:
            body.append(f"- … ve {len(warnings) - 8} tane daha")
    return body


def compose(run: Dict[str, Any]) -> Dict[str, Any]:
    """A bug draft for a failed run: title, body, code, severity and a frame."""
    code = classify(run)
    failed_step = _failed_scenario_step(run)
    agent_step = _failed_agent_step(run)

    body: List[str] = []
    body.append(f"**Scenario**  {run.get('title') or '—'}")
    if run.get("app_id"):
        body.append(f"**Where**  {run['app_id']}")
    body.append(f"**Cause**  {code} — {CODES.get(code, '')}")
    if code in NOT_APP_DEFECTS:
        body.append(
            "\n> This cause describes the run rather than the product — the step "
            "ran out of actions, timed out, or could not be proved. Check the "
            "scenario and the environment before filing it against the app."
        )

    if failed_step:
        body.append("")
        body.append(f"**Expected**  {failed_step.get('expected') or '—'}")
        body.append(f"**Actual**  {failed_step.get('message') or '—'}")
        # The step says the budget ran out; the action underneath says why it
        # ran out. The code is picked from the second, so the report has to
        # show it or the two read as contradicting each other.
        if agent_step and agent_step.get("message") != failed_step.get("message"):
            body.append(
                f"**Failing action**  {agent_step.get('action')} — "
                f"{agent_step.get('message') or '—'}"
            )
    elif agent_step:
        body.append("")
        body.append(f"**Actual**  {agent_step.get('message') or '—'}")

    steps = _reproduction(run)
    if steps:
        body.append("")
        body.append("**Steps to reproduce**")
        body.extend(steps)

    body.extend(_page_event_section(run))

    body.append("")
    body.append(f"_Raised from run {run.get('id')}._")

    return {
        "title": _title(run, code),
        "detail": "\n".join(body),
        "code": code,
        # A defect is worth what the scenario protecting it is worth. The
        # tester can overrule it; the default should not be "Medium always".
        "severity": run.get("priority") or "Medium",
        "runId": run.get("id"),
        "suiteRunId": run.get("suite_run_id"),
        "caseId": run.get("case_id"),
        "caseName": run.get("title"),
        "url": run.get("app_id"),
        "isAppDefect": code not in NOT_APP_DEFECTS,
    }


def screenshot_for(run: Dict[str, Any]) -> Optional[str]:
    """The frame that shows the failure.

    The failed step's own frame where there is one. Frames are not stored when
    they show what the previous step already showed, so this walks back to the
    last one that was — which is the screen the failure happened on either way.
    """
    steps = run.get("steps") or []
    failed_at = next(
        (i for i, step in enumerate(steps) if step.get("status") == "failed"),
        len(steps) - 1,
    )
    for step in reversed(steps[: failed_at + 1]):
        if step.get("screenshot"):
            return step["screenshot"]
    return None


def draft_for_run(run_id: str) -> Optional[Dict[str, Any]]:
    run = storage.get_run(run_id, include_screenshots=True)
    if run is None:
        return None
    draft = compose(run)
    # A run that passed has nothing to file. The tester can still raise one by
    # hand from the UI — sometimes a green run is wrong — but it goes through
    # a draft somebody reads, and `isAppDefect` is what decides whether one is
    # filed without anybody looking.
    if run.get("status") == "passed":
        draft["isAppDefect"] = False
        draft["passed"] = True
    draft["screenshot"] = screenshot_for(run)
    existing = storage.bug_for_run(run_id)
    draft["existingBugId"] = existing["id"] if existing else None
    return draft
