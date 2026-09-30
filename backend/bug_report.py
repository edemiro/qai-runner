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
from typing import Any, Dict, List, Optional, Tuple

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
    "SITE_BLOCKED": "The site's bot protection refused the session",
    "MODEL_UNAVAILABLE": "The AI model did not answer, so the step was never judged",
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
    # The site would not talk to this session. Nothing about the product was
    # tested, so nothing about the product can be wrong. The first purchase
    # run on the web was refused at its fourth step and filed as "the step's
    # expected result did not hold" — a defect, on a page it never reached.
    "SITE_BLOCKED",
    # The model was rate limited, out of credit or unreachable, so the step
    # was closed without anybody looking at the screen. Fourteen of the
    # hotel search bugs said only "Anthropic rate limit reached" and were
    # filed against the app as expectations that did not hold.
    "MODEL_UNAVAILABLE",
}

# What a provider says when it did not answer at all (see llm/). Matched on the
# message because that is all a finished step keeps of it.
_MODEL_FAILED = re.compile(
    r"rate limit reached|quota exhausted|temporarily overloaded"
    r"|(?:anthropic|openai|gemini) api error"
    r"|could not reach the (?:anthropic|openai|gemini) api"
    r"|rejected the api key|out of credit|has no credit"
    r"|spending budget is used up|budget has been exceeded|budget_exceeded"
    r"|returned an empty response|declined this step"
    r"|blocked this request with a safety filter",
    re.IGNORECASE,
)


def _model_failed(text: Optional[str]) -> bool:
    return bool(_MODEL_FAILED.search(text or ""))


def _action_cause(actions: List[Dict[str, Any]]) -> Optional[str]:
    """What the failed driver actions say went wrong, the first that says."""
    for step in actions:
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
    return None


# Status lines the runner writes when a step ends without a verdict of its own.
_NO_VERDICT = ("without reaching its expected result", "without verifying",
               "ended before this step closed")


def _failed_actions(run: Dict[str, Any], scenario_step: Optional[Dict[str, Any]] = None,
                    ) -> List[Dict[str, Any]]:
    """The driver actions that failed — only those serving `scenario_step`
    when one is given. A retry that failed inside a step which then passed is
    no evidence about a later step. An action recorded before actions carried
    their step is counted for whichever step asks."""
    failed = [s for s in run.get("steps") or [] if s.get("status") == "failed"]
    if scenario_step is None:
        return failed
    return [s for s in failed if s.get("scenario_idx") in (None, scenario_step.get("idx"))]


def classify(run: Dict[str, Any]) -> str:
    """The most specific cause the run's own record supports.

    Where the model judged the step, its verdict decides: it looked at the
    screen and said the expectation did not hold. The actions under the step
    only sharpen that — the text was not there, the element had gone — or,
    where the step ended without a verdict of its own, say why it ended.
    """
    # Before the evidence: a refused session fails whichever step it was on,
    # and that step's message says nothing about the refusal.
    if run.get("blocked"):
        return "SITE_BLOCKED"

    failed = [s for s in run.get("scenarioSteps") or [] if s.get("status") == "failed"]
    # A step the model never answered for was never judged, so it is no
    # evidence either way. Passed over rather than decided on: now that a
    # failed check no longer ends the run, an earlier step can hold a real
    # finding the later outage must not hide.
    judged = [s for s in failed if not _model_failed(s.get("message"))]
    if judged:
        step = judged[0]
        message = (step.get("message") or "").lower()
        cause = _action_cause(_failed_actions(run, step))
        if any(said in message for said in _NO_VERDICT):
            if cause:
                return cause
            # Out of actions, or left open when the run ended: no verdict was
            # reached, which is not the app failing it.
            return ("STEP_BUDGET_EXHAUSTED" if "without reaching" in message
                    else "STEP_UNPROVEN")
        # A timeout under a verdict is how the model found out, not the run
        # failing: a + pressed at its limit is greyed out and never answers.
        # Read as the cause, it filed "an action timed out" — not the app's —
        # for a limit whose warning never appeared.
        if cause and cause != "ACTION_TIMEOUT":
            return cause
        return "EXPECTATION_NOT_MET"
    if failed:
        return "MODEL_UNAVAILABLE"

    cause = _action_cause(_failed_actions(run))
    if cause:
        return cause

    error = (run.get("error") or "").lower()
    if _model_failed(error):
        return "MODEL_UNAVAILABLE"
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
    """The first step that failed on its merits, or failing that the first
    that failed at all — the same order classify() reads them in."""
    failed = [s for s in run.get("scenarioSteps") or [] if s.get("status") == "failed"]
    judged = [s for s in failed if not _model_failed(s.get("message"))]
    return (judged or failed or [None])[0]


def _failed_agent_step(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The first action that failed under the step that failed — the one the
    cause is read from, so the report shows what the code was picked from."""
    step = _failed_scenario_step(run)
    actions = _failed_actions(run, step) if step is not None else _failed_actions(run)
    return actions[0] if actions else None


def _summarise(text: Optional[str], limit: int = 120) -> str:
    """One line of it, with the sentence kept whole where it fits."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    boundary = max(cut.rfind(". "), cut.rfind(" — "), cut.rfind("; "))
    return (cut[:boundary] if boundary > 40 else cut).rstrip(" ,;—") + "…"


# --- the title -------------------------------------------------------------- #
#
# A bug is named after what went wrong; the scenario it was found in is said
# beside it, not in it. Titles used to be "<scenario> — step N: <action>": 161
# characters at the median, and the part a reader was scanning for — what is
# broken — was not in them at all. A list of those reads as a list of scenarios.

TITLE_LIMIT = 110

# The tool's own sentences come in a handful of fixed shapes, each cut to the
# part a reader triaging a list needs.
_TOOL_SHAPES = [
    (re.compile(r'^Expected "(?P<where>.*?)" to contain "(?P<want>.*?)"[.,]?\s+'
                r'(?:but\s+)?[Ii]t holds "(?P<got>.*?)"'),
     lambda m: (f'Expected "{m["want"]}", found "{m["got"]}"' if m["got"]
                else f'Expected "{m["want"]}", found nothing')),
    (re.compile(r'^Expected "(?P<want>.*?)" on screen but it is not there'),
     lambda m: f'"{m["want"]}" is not on the screen'),
    (re.compile(r'^\d+ page error\(s\).*?\bfirst:\s*(?:pageerror:\s*)?(?P<first>.+)$',
                re.IGNORECASE),
     lambda m: f'Page error: {m["first"]}'),
    (re.compile(r'^Visual "(?P<name>[^"]+)":\s*(?P<pct>[\d.]+%) of pixels differ'),
     lambda m: f'Screen "{m["name"]}" differs by {m["pct"]}'),
]

# "Expected: X. Seen: Y" — the second half is the finding.
_EXPECTED_THEN_SEEN = re.compile(
    r"^(?:beklenen|expected)\s*:.*?\b(?:görülen|gerçekleşen|actual|seen|got)\s*:\s*(?P<seen>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# Status lines that say the step ended, not what was wrong: the next piece of
# evidence names the defect better.
_SAYS_NOTHING = re.compile(
    r"used \d+ actions without reaching its expected result"
    r"|ended before this step closed|^\W*$",
    re.IGNORECASE,
)

# A negated or failed outcome. Turkish carries it in the verb — gelmedi,
# görünmüyor, çevrilmemiş, ulaşılamadı — so it is matched on the ending.
_FAILED_OUTCOME = re.compile(
    r"\w(?:m[ae]d[ıiuü]|m[ıiuü]yor|m[ae]m[ıiuü]ş)\b"
    r"|\b(?:yok|değil|yerine|hata\w*|not|never|no longer|instead|missing|cannot"
    r"|could not|unable|failed|error)\b"
)
# A state that is usually the problem but can as well be the expectation.
_SUSPECT_STATE = re.compile(
    r"\b(?:boş|pasif\w*|devre dışı|beklenen|expected|still|empty|disabled)\b"
)
_CONTRAST = re.compile(r",?\s+(?:ancak|fakat|ama|but|however)\s+", re.IGNORECASE)
_LEADING_CONTRAST = re.compile(r"^(?:ancak|fakat|ama|but|however)[,\s]+", re.IGNORECASE)
_CLAUSE_BREAK = re.compile(r"(?<!\d)\.\s+|;\s+|:\s+|\s+[—–]\s+")
# A quotation, not an apostrophe: Turkish puts one before every suffix on a
# name — "IST'yi", "4'te", "popover'ında" — and inside quotes too, as in
# 'EcoFly'da kal'. An opening quote follows no letter; a closing one is
# followed by none.
_QUOTED = re.compile(r"""(?<![\w'"])(?:'[^']*?(?:'\w[^']*?)*'|"[^"]*"|“[^”]*”)(?!\w)""")
# Pointers into the tool's view of the page. They mean nothing to whoever
# reads the bug and are the longest thing in many of these sentences.
_TOOL_ASIDE = re.compile(
    r"\s*\((?:[^()]|\([^()]*\))*?(?:\bel_\d+|assert_\w+|timeout)(?:[^()]|\([^()]*\))*\)"
)
_ELEMENT_REF = re.compile(r"\s*\bel_\d+\b")
_XPATH_BY_NAME = re.compile(r'//\*\[@[\w:-]+="([^"]+)"\]')
_XPATH_BY_PATH = re.compile(r"\bElement\s+/(?:hierarchy|AppiumAUT)\S*", re.IGNORECASE)


def _outcome(text: str) -> Tuple[bool, bool]:
    low = text.lower()
    return bool(_FAILED_OUTCOME.search(low)), bool(_SUSPECT_STATE.search(low))


def _is_expectation(text: str) -> bool:
    """"Beklenen ... olmasıydı" says what should have happened, not what did."""
    failed, _ = _outcome(text)
    return not failed and bool(re.match(r"^(?:beklenen|expected)\b", text, re.IGNORECASE))


def _pick_clause(clauses: List[str]) -> str:
    for clause in clauses:
        failed, suspect = _outcome(clause)
        if (failed or suspect) and not _is_expectation(clause):
            return clause
    return next((c for c in clauses if not _is_expectation(c)), clauses[0])


def _capitalised(text: str) -> str:
    if not text:
        return text
    first = {"i": "İ", "ı": "I"}.get(text[0], text[0].upper())
    return first + text[1:]


def _shortened_quote(quoted: str, limit: int = 40) -> str:
    """A long quotation cut inside its quotes, so the sentence around it —
    which is what says what went wrong — still fits."""
    inner = quoted[1:-1]
    if len(inner) <= limit:
        return quoted
    cut = inner[: limit - 4]
    space = cut.rfind(" ")
    return quoted[0] + (cut[:space] if space > limit // 2 else cut).rstrip(" ,.;:") + "…" + quoted[-1]


def _tidy(line: str) -> Optional[str]:
    line = _QUOTED.sub(lambda m: _shortened_quote(m.group(0)), line)
    line = " ".join(line.split()).strip(" .,;:—–-")
    if not line:
        return None
    if len(line) > TITLE_LIMIT:
        cut = line[: TITLE_LIMIT - 1]
        space = cut.rfind(" ")
        line = (cut[:space] if space > TITLE_LIMIT // 2 else cut).rstrip(" ,;:—–-") + "…"
    return _capitalised(line)


def headline(text: Optional[str]) -> Optional[str]:
    """The failure in a line: the clause of `text` that says what was wrong.

    `text` is what the run said when the step failed — usually the model's own
    verdict, "Yenileme sonrası son geçerli form korunmadı: Nereye alanı ...",
    where the first clause is the finding and the rest the evidence for it.
    Where the verdict opens with what worked ("öneriler geldi ancak ülke
    adları ham çeviri anahtarı olarak görünüyor"), the part after the "but" is
    the one kept. None when there is nothing to say.
    """
    flat = " ".join((text or "").split())
    if not flat or _SAYS_NOTHING.search(flat):
        return None

    flat = _XPATH_BY_NAME.sub(r'"\1"', flat)
    flat = _XPATH_BY_PATH.sub("An element", flat)
    for shape, render in _TOOL_SHAPES:
        match = shape.search(flat)
        if match:
            return _tidy(render(match))
    seen = _EXPECTED_THEN_SEEN.match(flat)
    if seen:
        flat = seen.group("seen")

    # Quoted text is held aside while the sentence is cut up, so a full stop
    # or a colon inside a quoted message does not split it, and a "gelmedi"
    # inside one does not read as the outcome.
    quotes: List[str] = []

    def hold(match: "re.Match[str]") -> str:
        quotes.append(match.group(0))
        return f"\x00{len(quotes) - 1}\x00"

    held = _QUOTED.sub(hold, flat)
    held = _TOOL_ASIDE.sub("", held)
    held = _ELEMENT_REF.sub("", held)

    clauses = [c.strip() for c in _CLAUSE_BREAK.split(held) if c and c.strip()]
    if not clauses:
        return None
    clause = _LEADING_CONTRAST.sub("", _pick_clause(clauses))

    contrast = _CONTRAST.search(clause)
    if contrast:
        before, after = clause[: contrast.start()], clause[contrast.end():]
        if not any(_outcome(before)) and any(_outcome(after)):
            clause = after

    return _tidy(re.sub(r"\x00(\d+)\x00", lambda m: quotes[int(m.group(1))], clause))


def _title(run: Dict[str, Any], code: str) -> str:
    """What went wrong, in a line a list can be scanned by.

    Taken from the most telling thing the run said: the step's verdict, then
    the action that failed under it, then the run's own error — and if none of
    them says anything, what the cause code means.
    """
    failed = _failed_scenario_step(run)
    agent_step = _failed_agent_step(run)
    for said in (
        failed.get("message") if failed else None,
        agent_step.get("message") if agent_step else None,
        run.get("error"),
    ):
        line = headline(said)
        if line:
            return line
    return CODES.get(code) or "The scenario failed"


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
    if code == "MODEL_UNAVAILABLE":
        body.append(
            "\n> This cause describes the run rather than the product — the AI "
            "model did not answer (rate limited, out of credit or unreachable), "
            "so nobody looked at the screen for this step. Run the scenario "
            "again before reading anything into it."
        )
    elif code in NOT_APP_DEFECTS:
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


def run_failure(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Why a run did not pass, in the words a list row and a report need.

    The same reading a bug is written from — the step the model judged, the
    action under it, the cause — so a row on Test Runs and the bug raised from
    that run say the same thing. None for a run that passed or is still going.
    """
    if run.get("status") not in ("failed", "cancelled"):
        return None
    code = classify(run)
    step = _failed_scenario_step(run)
    # Stopped with nothing failed, or refused by the site: nothing was judged,
    # so what the last action said — a click cut off by Stop — is not why it
    # ended. A run stopped after a step had already failed keeps that finding.
    if code == "SITE_BLOCKED" or (run.get("status") == "cancelled" and step is None):
        code = code if code == "SITE_BLOCKED" else "RUN_CANCELLED"
        return {"headline": CODES[code], "step": None, "expected": None, "actual": None,
                "failingAction": None, "code": code, "codeText": CODES[code],
                "isAppDefect": False}
    action = _failed_agent_step(run)
    said = step.get("message") if step else None
    return {
        "headline": _title(run, code),
        "step": step.get("idx") if step else None,
        "expected": step.get("expected") if step else None,
        "actual": said or (action.get("message") if action else None) or run.get("error"),
        # Only when it says something the step did not.
        "failingAction": (
            {"action": action.get("action"), "message": action.get("message")}
            if action and step and action.get("message") != said else None
        ),
        "code": code,
        "codeText": CODES.get(code),
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
    # The Test Set, which is what Bug Report groups by. Read off the scenario
    # because the run does not carry it, and copied into the bug because the
    # set can be renamed or deleted after.
    found = storage.cases_by_id([run["case_id"]]) if run.get("case_id") else []
    draft["suiteName"] = found[0].get("suite_name") if found else None
    return draft


# --- reading a body back ------------------------------------------------------ #

# The first line of a bug the runner filed itself.
AUTO_RAISED = ("Raised automatically when this scenario failed. Nobody has "
               "reviewed it yet.")

FIELD_KEYS = {
    "Scenario": "scenario", "Where": "where", "Cause": "cause",
    "Expected": "expected", "Actual": "actual", "Failing action": "failing",
}
EVENT_SECTIONS = {
    "Sayfa hataları": "error", "Sayfa uyarıları": "warning",
    "Page errors": "error", "Page warnings": "warning",
}
_FIELD_LINE = re.compile(r"^\*\*(?P<label>[^*]+)\*\*\s+(?P<value>\S.*)$")
_HEADING_LINE = re.compile(
    r"^\*\*(?P<label>[^*]+)\*\*(?:\s+\((?P<count>\d+)\))?(?:\s+—\s+(?P<hint>.+))?$"
)
_STEP_LINE = re.compile(r"^(?P<mark>[✓✗])\s+(?P<idx>\d+)\.\s+(?P<action>.*)$")
_STEP_EXPECTED = re.compile(r"^expected:\s*(?P<text>.*)$")
_MORE_LINE = re.compile(r"^-\s+(?P<text>(?:…|\.\.\.).*)$")
_EVENT_LINE = re.compile(r"^-\s+(?P<kind>[^:]+?):\s*(?P<text>.*)$")
_RAISED_FROM = re.compile(r"^_Raised from run (?P<run>[^\s_]+)\._$")


def parse_detail(detail: Optional[str]) -> Dict[str, Any]:
    """A body `compose` wrote, read back into its parts.

    Stored as text, because text is what a tracker takes and what a tester
    edits before raising it. Shown as parts — each label in its own colour
    beside what it says, the steps as a list, the page's complaints folded
    away — because a wall of monospace is where the one line that matters gets
    lost. Whatever is not recognised is kept whole in `rest`, so a bug written
    by hand, or edited out of this shape, still reads in full.
    """
    parts: Dict[str, Any] = {
        "autoRaised": False, "fields": {}, "notes": [], "steps": [],
        "events": [], "runId": None, "rest": None,
    }
    rest: List[str] = []
    section: Any = None       # "steps", or the page-event section being read
    field: Optional[str] = None  # the field a value running onto the next line belongs to

    for raw in (detail or "").splitlines():
        line = raw.strip()
        if not line:
            section = field = None
            # A paragraph break inside text kept whole is part of the text.
            if rest and rest[-1]:
                rest.append("")
            continue
        if line == AUTO_RAISED:
            parts["autoRaised"] = True
            continue
        match = _RAISED_FROM.match(line)
        if match:
            parts["runId"] = match.group("run")
            continue
        if line.startswith(">"):
            parts["notes"].append(line.lstrip("> ").strip())
            section = field = None
            continue
        match = _FIELD_LINE.match(line)
        if match and match.group("label").strip() in FIELD_KEYS:
            field = FIELD_KEYS[match.group("label").strip()]
            parts["fields"][field] = match.group("value").strip()
            section = None
            continue
        match = _HEADING_LINE.match(line)
        if match:
            label = match.group("label").strip()
            if label == "Steps to reproduce":
                section, field = "steps", None
                continue
            if label in EVENT_SECTIONS:
                section = {
                    "level": EVENT_SECTIONS[label], "label": label,
                    "count": int(match.group("count") or 0),
                    "hint": match.group("hint"), "items": [], "more": None,
                }
                parts["events"].append(section)
                field = None
                continue
        if section == "steps":
            match = _STEP_LINE.match(line)
            if match:
                parts["steps"].append({
                    "idx": int(match.group("idx")), "action": match.group("action"),
                    "failed": match.group("mark") == "✗", "expected": None,
                })
                continue
            match = _STEP_EXPECTED.match(line)
            if match and parts["steps"]:
                parts["steps"][-1]["expected"] = match.group("text")
                continue
        elif isinstance(section, dict):
            match = _MORE_LINE.match(line)
            if match:
                section["more"] = match.group("text")
                continue
            if line.startswith(("http://", "https://")) and section["items"]:
                section["items"][-1]["url"] = line
                continue
            match = _EVENT_LINE.match(line)
            if match:
                section["items"].append(
                    {"kind": match.group("kind"), "text": match.group("text"), "url": None})
                continue
        if field is not None:
            parts["fields"][field] += "\n" + line
            continue
        rest.append(raw.rstrip())

    parts["rest"] = "\n".join(rest).strip() or None
    return parts


def title_from_detail(detail: Optional[str], code: Optional[str] = None) -> Optional[str]:
    """What `_title` would call a bug, from the body it was filed with.

    For bugs filed before titles said what went wrong: the run they came from
    may be gone, but the body kept everything the title is made from.
    """
    parts = parse_detail(detail)
    fields = parts["fields"]
    failing = fields.get("failing") or ""
    # "<action> — <what it said>": the action's name is not the finding.
    failing_said = failing.split(" — ", 1)[1] if " — " in failing else failing
    for said in (fields.get("actual"), failing_said):
        line = headline(said)
        if line:
            return line
    first_error = next((item["text"] for section in parts["events"]
                        if section["level"] == "error" for item in section["items"]), None)
    if code == "PAGE_ERRORS" and first_error:
        return _tidy(f"Page error: {first_error}")
    return CODES.get(code or "") or None


def _named_after_its_scenario(bug: Dict[str, Any]) -> bool:
    """Whether the title is the one the old composer wrote, word for word:
    "<scenario> — step N: <action>". One a tester typed is theirs to keep."""
    scenario = parse_detail(bug.get("detail"))["fields"].get("scenario")
    return bool(scenario) and (bug.get("title") or "").startswith(
        _summarise(scenario, 90) + " — ")


def refresh_titles() -> int:
    """Rename the bugs still named after their scenario. Returns how many.

    Safe to run at every start: a renamed bug no longer starts with its
    scenario, so it is not picked up again.
    """
    renamed = 0
    for bug in storage.list_bugs(limit=1_000_000):
        if not _named_after_its_scenario(bug):
            continue
        title = title_from_detail(bug.get("detail"), bug.get("code"))
        if title and title != bug.get("title"):
            storage.update_bug(bug["id"], title=title)
            renamed += 1
    return renamed
