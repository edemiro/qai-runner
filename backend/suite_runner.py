"""Run a whole suite: many cases, optionally many at once.

A single agent run is a demo; a suite is what a team actually puts in CI. The
differences that matter are all here — cases are selected by tag, a case with a
dataset becomes one execution per row, each execution gets its own isolated
browser so nothing leaks between them, and a bounded pool decides how many run
at the same time.

Every execution is recorded as an ordinary run, so the existing report, export
and replay all work on it unchanged.
"""

import asyncio
import calendar
import json
import os
import re
import shutil
import time
import traceback
from datetime import date, timedelta
from typing import Any, AsyncGenerator, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse
from uuid import uuid4

import agent
import drivers
import bug_report
import mobile_session
import storage
import config
from drivers.web import WebTarget, run_artifact_dir

# More than this and the machine, not the site, becomes the bottleneck — each
# worker is a full browser with its own renderer processes.
MAX_WORKERS = 8

# One definition of what a {{name}} looks like: storage reads them too, to
# tell which recorded values came from the store.
PLACEHOLDER = storage.PLACEHOLDER

# The tag a scenario carries when it needs the app to have forgotten. Written
# as a tag rather than a field of its own because tags are already editable
# beside the scenario, and this is a property of the scenario in the same way
# "smoke" is — not a new dimension of one.
FRESH_APP = "fresh-app"


def substitute(text: Optional[str], row: Optional[Dict[str, Any]],
               shared: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Fill {{placeholders}} from the case's own row, then from the store.

    The row wins. A dataset says what is different about this run of the case —
    which payment method, which route — and the store says what is the same
    everywhere: the test card, the test account. A case that names both gets
    its own answer, so a row can override a shared value without editing it for
    everybody.

    An unknown placeholder is left as written rather than blanked, so a typo
    shows up in the report as `{{emial}}` instead of silently becoming "".
    """
    if not text:
        return text
    if not row and not shared:
        return text

    def resolve(match):
        name = match.group(1)
        if row and name in row:
            return computed(str(row[name]))
        if shared and name in shared:
            return computed(str(shared[name]))
        return match.group(0)

    # As text, the way `storage.clean_recorded` keeps it: a recording sent
    # straight to the API can say `"value": 2`, and that was a 500.
    return PLACEHOLDER.sub(resolve, str(text))


# A value that is worked out when the run starts rather than stored: `today`,
# `today+7`, `bugün-1`, `today-8y`, `today-10m`. Days unless a unit says
# otherwise; either language, spaces allowed.
COMPUTED = re.compile(
    r"^\s*(today|bug[üu]n)\s*(?:([+-])\s*(\d{1,4})\s*([dmy]|g[üu]n|ay|y[ıi]l)?)?\s*$",
    re.IGNORECASE)

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def computed(value: str, today: Optional[date] = None) -> str:
    """A stored value that names a day relative to now, made concrete.

    A scenario that says "pick a date a few days from today" was recorded
    picking the 25th and checking for "25 Eyl", which was right for a week.
    The step is about *when*, and a recording can only hold *what*. So the
    date is not written into the step at all: the step names `{{tarih.gidis}}`,
    the store holds `today+7`, and the run gets "4 October 2026" — a concrete
    target for the model and a correct assertion, on the day it runs.

    The step's words change with the day, so it is one the model carries out
    rather than replays. That is the trade: a model call a run for the one
    step that genuinely is different every day, and recordings for the rest.

    Written as "4 October 2026" — day first, month in full — because it is
    read by a model looking at a calendar, and that form maps onto every
    screen this runs against: "4 Eki", "4 OCT 2026", "04.10.2026".
    """
    found = COMPUTED.match(value or "")
    if not found:
        return value
    amount = int(found.group(3) or 0) * (-1 if found.group(2) == "-" else 1)
    unit = (found.group(4) or "d").lower()
    base = today or date.today()
    # Years and months are for ages: a child's date of birth kept as
    # `today-8y` is a child's on every run, where a date typed in once turns
    # into an adult's one day and nobody is told.
    if unit in ("y", "yıl", "yil"):
        when = _months_on(base, amount * 12)
    elif unit in ("m", "ay"):
        when = _months_on(base, amount)
    else:
        when = base + timedelta(days=amount)
    return f"{when.day} {MONTHS[when.month - 1]} {when.year}"


def _months_on(day: date, months: int) -> date:
    """The same day of the month, `months` on — or the last day the month
    has, where that day does not exist: 31 January and a month is 28 (or
    29) February."""
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))


def with_precondition(goal: str, precondition: Optional[str]) -> str:
    """Put the scenario's required starting state in front of its goal.

    Stored separately so a reviewer can see it and edit it, but the agent only
    ever receives the goal — so unless it is folded in here, the setup is
    written down and then never acted on, which is worse than not having the
    field at all.
    """
    text = " ".join((precondition or "").split())
    if not text:
        return goal
    return f"Precondition (make this true before starting): {text}\n\n{goal}"


def substitute_steps(
    steps: Optional[List[Dict[str, str]]], row: Optional[Dict[str, Any]],
    shared: Optional[Dict[str, str]] = None,
) -> Optional[List[Dict[str, str]]]:
    """Fill a dataset row into a scenario's steps, as it already is into its goal.

    Without this a data-driven case ran N times with the literal text
    `{{route}}` in every instruction — the goal and the URL were substituted
    and the steps, which are what the agent actually carries out, were handed
    over raw. The case editor invites exactly this: it tells the tester to use
    {{placeholders}} in the field directly above the steps.
    """
    if not steps or (not row and not shared):
        return steps or None
    filled = []
    for step in steps:
        # Copied, not rebuilt. Listing the fields to keep meant every field
        # not listed was silently dropped here: `optional` went that way once,
        # and `recorded` went the same way the moment the shared store made
        # this run for every case rather than only for data-driven ones. A
        # booking scenario with eleven recorded steps replayed none of them
        # and cost 471,000 tokens and fifty-six model calls to walk a flow it
        # already knew.
        replaced = dict(step)
        action = (substitute(step.get("action"), row, shared)
                  or step.get("action"))
        expected = substitute(step.get("expected"), row, shared)
        replaced["action"], replaced["expected"] = action, expected

        # Except where the *row* changed the step's own words. The recording
        # typed what the step said under the previous row, so replaying it
        # would fill in that row's passenger — right actions, wrong values,
        # and green. Those steps go back to the model.
        #
        # The shared store is not that. Its values are the same on every run,
        # and a recording names them rather than spelling them out (see
        # storage.named_not_spelled), so it is filled in here the way the
        # step's own words were. This used to drop the recording for any
        # step whose words changed at all, which was every step that used a
        # name from the store — the passenger form, eleven actions long,
        # went back to the model on every run of a scenario it already knew.
        if row and (substitute(step.get("action"), row) != step.get("action")
                    or substitute(step.get("expected"), row) != step.get("expected")):
            replaced.pop("recorded", None)
        elif replaced.get("recorded"):
            replaced["recorded"] = [
                {**item, "value": substitute(item.get("value"), row, shared)}
                if item.get("value") else item
                for item in replaced["recorded"]
            ]
        filled.append(replaced)
    return filled


def expand_cases(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One execution per case, or per dataset row when the case has one."""
    executions = []
    for case in cases:
        dataset = case.get("dataset")
        if not dataset:
            executions.append({"case": case, "row": None, "label": case["name"]})
            continue
        for index, row in enumerate(dataset, start=1):
            if not isinstance(row, dict):
                continue
            # A named column makes the report readable; otherwise fall back to
            # the row number so two executions are never indistinguishable.
            hint = row.get("name") or row.get("label") or " / ".join(
                f"{k}={v}" for k, v in list(row.items())[:2]
            )
            executions.append({
                "case": case, "row": row,
                "label": f"{case['name']} [{hint or index}]",
            })
    return executions


def apply_environment(url: Optional[str], env_url: Optional[str]) -> Optional[str]:
    """Point a case at the chosen environment, keeping the path it carries.

    A scenario stores the address it was written against, and the same scenario
    has to be runnable on test, preprod and prod without editing forty of them.
    The environment replaces the origin and nothing else: `/tr-tr/flights` on
    nuat becomes `/tr-tr/flights` on whichever stack was picked.

    Anything it cannot parse it leaves alone, so a case with an odd address
    fails exactly as loudly as it does today rather than quietly running
    somewhere else.
    """
    if not env_url:
        return url
    if not url:
        return env_url
    chosen = urlparse(env_url)
    if not chosen.scheme or not chosen.netloc:
        return url
    if url.startswith("/"):
        # Stored as a path rather than a full address.
        return urljoin(env_url, url)
    current = urlparse(url)
    if not current.scheme:
        # "nuat.turkishairlines.com/tr-tr" parses as all-path, so the host has
        # to be rescued before the origin can be swapped for it.
        current = urlparse("https://" + url)
    if not current.netloc:
        return url
    return urlunparse((
        chosen.scheme, chosen.netloc, current.path,
        current.params, current.query, current.fragment,
    ))


def _event(kind: str, **payload) -> str:
    return json.dumps({"event": kind, **payload}, ensure_ascii=False) + "\n"


def _case_result(case: Dict[str, Any], run_id: Optional[str], label: str, status: str,
                 error: Optional[str], started: float,
                 store_keys: Iterable[str] = ()) -> Dict[str, Any]:
    """What a case's `case_finished` says, and what the execution's totals count.

    The error named as the run's record names it: it goes to the watching
    page and into the CI report, and can quote what the store filled in.
    """
    return {
        "caseId": case["id"], "runId": run_id, "label": label,
        "status": status, "error": storage.named(error, store_keys),
        "durationMs": int((time.time() - started) * 1000),
    }


class _RunControl:
    """The handle a running execution can be stopped by.

    An execution runs detached from whoever started it, which is what stopped
    runs dying with the client's stream — but it also meant nothing held a
    reference to one, so "stop" could only ever have meant hiding it. This is
    that reference: the flag pending cases check before they open a browser,
    the tasks to cut short if a case will not wind down, and the agent states
    of mobile cases, which run on a throwaway session the usual cancel cannot
    reach.
    """

    def __init__(self) -> None:
        self.stop = asyncio.Event()
        self.tasks: List[asyncio.Task] = []
        self.states: List[Any] = []


# suite_run_id -> control. Only while the run is actually in flight.
_RUNNING: Dict[str, _RunControl] = {}

# The phones executions are using, by session id: {"suiteRunId", "name"}. A
# mobile case runs on a throwaway agent state (see `_run_mobile_case`), so the
# session itself looked idle for the whole execution: Run started a second
# agent on the same phone, and Disconnect closed the session under the cases.
_CLAIMED: Dict[str, Dict[str, Any]] = {}


def claimed_by(session_id: str) -> Optional[Dict[str, Any]]:
    """The execution using this device session, or None."""
    return _CLAIMED.get(session_id)

# How long a case is given to stop of its own accord before its task is cut.
# An agent checks for cancellation between steps, and a step is mostly one
# model call, so a clean stop usually lands within a few seconds — but a
# gateway that has stopped answering would otherwise hold the execution open
# for its whole timeout.
STOP_GRACE_S = 15


def is_running(suite_run_id: str) -> bool:
    return suite_run_id in _RUNNING


async def cancel(suite_run_id: str) -> bool:
    """Stop an execution for real: no more cases, and the live ones wound down.

    Returns False when the run is not in flight here — already finished, or
    started by a process that is no longer around.
    """
    control = _RUNNING.get(suite_run_id)
    if control is None:
        return False

    control.stop.set()

    # Every browser this run has open belongs to a case mid-flight. Asking its
    # agent to stop is the clean route: it finishes the call it is waiting on
    # and closes the run properly, with its steps recorded.
    for session_id in list(drivers.all_targets()):
        owner = drivers.owner(session_id)
        if owner and owner.get("suiteRunId") == suite_run_id:
            agent.cancel(session_id)

    # A mobile case runs on a throwaway agent session that is in no registry,
    # so it is cancelled through the state itself.
    for state in control.states:
        state.cancel.set()

    async def cut_off() -> None:
        await asyncio.sleep(STOP_GRACE_S)
        for task in control.tasks:
            if not task.done():
                task.cancel()

    asyncio.create_task(cut_off())
    return True


def _keep_what_worked(
    run_id: Optional[str], case: Dict[str, Any], row: Optional[Dict[str, Any]],
) -> None:
    """Hand a green run's actions back to the scenario that produced them.

    The second run of an unchanged scenario has no reason to ask the model how
    to do what the first one already did, so what worked is kept on the steps
    themselves and replayed next time.

    Not for a dataset row, though. The recorded actions carry the values that
    were typed, and those came from that row — replaying them under the next
    row would fill the form with the previous passenger and then assert against
    the wrong one. A scenario driven by data is re-derived each time.

    Best-effort throughout: a scenario that cannot learn from a run is not a
    reason to fail the run.
    """
    if not run_id or row:
        return
    try:
        # The steps as they were when the case started, not as they are now:
        # the scenario can be edited while it runs (see promote_recording).
        kept = storage.promote_recording(run_id, case["id"], ran_steps=case.get("steps"))
        if kept:
            print(f"[suite] kept the recording for {kept} step(s) of {case['id']}")
    except Exception as exc:  # noqa: BLE001
        print(f"[suite] could not keep the recording for {case['id']}: {exc}")


def _raise_bug_if_it_found_one(
    run_id: Optional[str],
    case: Dict[str, Any],
    suite_run_id: Optional[str] = None,
    suite_name: Optional[str] = None,
) -> None:
    """File what a failed scenario turned out to mean, while it is still known.

    Only when the classifier calls it a defect of the app. A scenario that ran
    out of actions, closed a step without proving it, or was stopped by hand
    has failed for a reason that belongs to the run, and a tracker that mixes
    those with real findings is a tracker whose triage is worthless — which is
    why bugs raised by hand still go through a draft somebody reads.

    Raised open and said to be unreviewed, because nobody has looked at it: QAi
    is wrong often enough — an expected string no page renders, a limit a
    scenario invented — that an automatic bug is a lead, not a finding.

    Once per scenario, not once per run. A bug belongs to the scenario it was
    found in, and every turn is a new run: three turns of a measurement filed
    three identical rows, which is how a tracker stops being worth reading.
    """
    if not run_id:
        return
    try:
        draft = bug_report.draft_for_run(run_id)
        if not draft or not draft.get("isAppDefect") or draft.get("existingBugId"):
            return
        if draft.get("passed"):
            # Belt and braces with the same check in draft_for_run: this used
            # to be called after every case whatever it did, and a scenario
            # that passed all four of its steps had a bug filed against the
            # app saying the run failed for no recognised reason.
            return
        already = storage.open_bug_for_case(case.get("id"), draft.get("code"))
        if already:
            print(f"[suite] already raised as {already['id']}: "
                  f"{case.get('name', run_id)[:50]}")
            return
        storage.create_bug(
            title=draft["title"],
            detail=bug_report.AUTO_RAISED + "\n\n" + (draft.get("detail") or ""),
            code=draft.get("code"), severity=draft.get("severity"),
            run_id=run_id, suite_run_id=suite_run_id,
            case_id=case.get("id"), case_name=case.get("name"),
            suite_name=case.get("suite_name") or suite_name or draft.get("suiteName"),
            url=draft.get("url"), screenshot=draft.get("screenshot"),
        )
        print(f"[suite] raised a bug for {case.get('name', run_id)[:60]}")
    except Exception as exc:  # noqa: BLE001
        print(f"[suite] could not raise a bug for {run_id}: {exc}")


def _record_unstarted_case(
    case: Dict[str, Any],
    suite_run_id: str,
    kind: str,
    error: Optional[str],
    row: Optional[Dict[str, Any]],
    label: str,
    status: str = "failed",
    # What the case was handed from the store: the reason can quote an address
    # the store was filled into (see storage._secrets_for).
    store_keys: Iterable[str] = (),
) -> str:
    """Give a case that died before the agent started a run of its own.

    The agent owns run creation, so a case that fails on the way there — a
    browser the site refuses, a device that dropped, a URL that will not
    resolve — used to leave nothing behind at all: the reason went out on the
    live stream and was gone, and the execution came back `failed` with zero
    scenarios. That reads as "nothing ran" when in fact everything ran and
    everything broke, and the JUnit report counted `tests="0" failures="0"`,
    which a build server calls green. A row here costs one insert and makes the
    failure survive the run that produced it.
    """
    run_id = storage.create_run(
        case.get("goal") or case.get("name") or label,
        kind=kind,
        tags=case.get("tags") or [],
        suite_run_id=suite_run_id,
        case_id=case["id"],
        dataset_row=row,
        store_keys=store_keys,
    )
    storage.link_run_to_suite(
        run_id, suite_run_id, case["id"],
        title=label, tags=case.get("tags") or [],
        dataset_row=row, kind=kind,
        priority=case.get("priority"), layer=case.get("layer"),
        case_idx=case.get("idx"),
    )
    if status == "cancelled":
        reason = error or "Stopped before the run started."
    else:
        status, reason = "failed", error or "The run never started and gave no reason."
    storage.finish_run(run_id, status, reason, verdict_note=reason, store_keys=store_keys)
    return run_id


def _stopped_or(status: str, verdict: Optional[str], control: Optional[_RunControl]) -> str:
    """The case's status, unless Stop reached it before it had a verdict.

    A stopped case is not a failed one. The agent records its own run as
    cancelled, and the runner then closed it a second time as "failed" — its
    starting value, and all a case cut off by Stop ever reaches — which also
    filed an automatic bug against the app for a run the tester had stopped.
    A verdict the case did reach stands, stop or not.
    """
    if status == "cancelled":
        return status
    if control is not None and control.stop.is_set() and verdict in (None, "cancelled"):
        return "cancelled"
    return status


async def _execute_one(
    execution: Dict[str, Any],
    suite_run_id: str,
    options: Dict[str, Any],
    emit: "asyncio.Queue[str]",
) -> Dict[str, Any]:
    """Run one case (or one dataset row of it) in its own browser."""
    case = execution["case"]
    row = execution["row"]
    label = execution["label"]

    # Read once per case, so a value edited between cases takes effect on the
    # next one rather than at the next restart.
    shared = storage.test_data_values()
    goal = with_precondition(
        substitute(case["goal"], row, shared) or case["goal"],
        substitute(case.get("precondition"), row, shared),
    )
    url = apply_environment(
        substitute(case.get("url") or options.get("base_url"), row, shared),
        options.get("env_url"),
    )
    # What the case is handed from the store — see `storage._secrets_for`.
    store_keys = storage.referenced_keys(
        case.get("goal"), case.get("precondition"), case.get("steps"),
        case.get("url") or options.get("base_url"),
    )

    run_id: Optional[str] = None
    target: Optional[WebTarget] = None
    status = "failed"
    error: Optional[str] = None
    # Whether the site refused this run its data. Kept apart from the status
    # so a pass rate measures the product rather than the mood of its bot
    # protection — the first time the two were mixed, a critical bug was
    # raised against an airline for a button that was grey only because a
    # data call had been refused.
    was_blocked = False
    started = time.time()

    # Checked before anything is opened: a stopped execution must not spend a
    # browser, a model call or a row on the cases still queued behind it.
    control = options.get("control")
    if control is not None and control.stop.is_set():
        result = {
            "caseId": case["id"], "runId": None, "label": label,
            "status": "cancelled", "error": None, "durationMs": 0,
        }
        await emit.put(_event("case_finished", **result))
        return result

    await emit.put(_event("case_started", case=case["id"], label=label,
                          url=storage.named(url, store_keys)))

    # The video directory has to be chosen before the browser starts, but the
    # run id only exists once the agent has created it — so recordings go under
    # the case, and are moved into the run's folder afterwards. Assigned before
    # the try because `finally` cleans it up: raising for a missing URL inside
    # the try left it unbound, and the UnboundLocalError that followed escaped
    # `_execute_one` entirely — gather() swallowed it, the case produced no
    # `case_finished` and no result row, and a suite whose other cases passed
    # was reported green while silently dropping that one.
    staging = run_artifact_dir(f"case-{case['id']}-{uuid4().hex[:8]}")
    # The verdict the agent reached, if it reached one. Read in `finally`, so
    # it is assigned before anything that can raise.
    last_status = None
    cut_off = False

    try:
        if not url:
            raise ValueError("The case has no URL and no base URL was given.")

        target = await WebTarget.launch(
            url,
            headless=options.get("headless", config.RUN_HEADLESS_DEFAULT),
            width=options.get("width", 1440),
            height=options.get("height", 900),
            auth_profile=case.get("auth_profile") or options.get("auth_profile"),
            record_video_dir=os.path.join(staging, "video")
            if options.get("record_video") else None,
        )

        # Registered, so the mirror can reach it: an execution used to run in a
        # browser no route knew about, which is why starting one left the
        # tester watching a status chip and nothing else. The owner marks it as
        # the run's browser rather than a page the tester opened, so the
        # workspace shows it read-only instead of offering to close it.
        drivers.register(target, owner={
            "suiteRunId": suite_run_id,
            "name": options.get("execution_name"),
            "caseId": case["id"],
            "label": label,
            "idx": case.get("idx"),
        })

        if options.get("trace"):
            await target.start_trace()

        async for line in agent.run_agent(
            target, goal,
            # Left as None unless the caller asked for a ceiling: a written
            # scenario gets one scaled to its own length, and passing the
            # free-roaming 40 here would cap an 8-step case at 40 actions when
            # a single step may spend 12. The same case run from the workspace
            # sends None and gets the full budget — the verdict must not depend
            # on which button started it.
            max_steps=options.get("max_steps"),
            use_vision=options.get("use_vision", True),
            # A case written out as steps is run step by step and judged the
            # same way; one without them keeps the open-ended behaviour.
            steps=substitute_steps(case.get("steps"), row, shared),
            store_keys=store_keys,
        ):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            # The agent creates and owns the run record — it is what writes the
            # steps — so the runner adopts that run instead of opening a second
            # one that would hold only half the report.
            if payload.get("runId") and not run_id:
                run_id = payload["runId"]
                storage.link_run_to_suite(
                    run_id, suite_run_id, case["id"],
                    title=label, tags=case.get("tags") or [],
                    dataset_row=row, kind="web",
                    priority=case.get("priority"), layer=case.get("layer"),
                    case_idx=case.get("idx"),
                )
            # Step-level chatter would interleave unreadably across workers;
            # only the case's own outcome is forwarded.
            if payload.get("event") == "finished":
                last_status = payload.get("status")
            # A scenario the tester stopped is not one that failed. Without
            # this the `cancelled` event went unread, last_status stayed None
            # and the case fell through to "failed" — a stopped run reported
            # as a broken one.
            elif payload.get("event") == "cancelled":
                last_status = "cancelled"
                error = payload.get("message")
            elif payload.get("event") == "error":
                error = payload.get("message")

        status = last_status or "failed"

        artifacts_dir = run_artifact_dir(run_id) if run_id else staging
        # Named before anything below quotes them: the refusal and page-error
        # lines cut the text short, and a card cut in half is no longer the
        # card storage looks for.
        events = storage.named_page_events(target.drain_events())
        if run_id:
            storage.add_page_events(run_id, events)

        # Someone else's server failing is not this test's result. The intent
        # was already written down on _is_third_party — "their outages are not
        # the test's problem" — but nothing consulted the flag, so an analytics
        # host having a bad day failed every scenario in the set. They stay in
        # the report either way; they just no longer decide the verdict.
        page_errors = [
            e for e in events
            if e.get("level") == "error" and not e.get("thirdParty")
        ]

        # A refusal by the site's bot protection is said plainly, whatever the
        # verdict was, because it is the one failure a tester cannot act on by
        # reading the scenario: the run did everything right and was not
        # allowed to. Reported ahead of the page-error line so it is not filed
        # away as one — and ahead of a green verdict too, since a run that was
        # refused its data did not demonstrate anything.
        refusals = [e for e in events if e.get("kind") == "blocked"
                    and not e.get("thirdParty")]
        if refusals:
            status = "failed"
            was_blocked = True
            error = (
                "The site's bot protection refused this session. The call"
                " named is the first one refused, not the only one — a"
                " refusal here applies to the session, so the screens after"
                " it cannot fill in either. First refused: "
                + f"{refusals[0].get('url', '')[:120]}. "
                + str(refusals[0].get("text", ""))[:400]
            )
        elif status == "passed" and page_errors and options.get("fail_on_page_error", True):
            # A run that clicked through happily while the console threw and an
            # API returned 500 has not demonstrated that the feature works.
            status = "failed"
            error = (
                f"{len(page_errors)} page error(s) while the run was green — "
                f"first: {page_errors[0].get('text', '')[:160]}"
            )

        # A trace with no run to hang it off cannot be reached from the report,
        # so it is only written once the run has been adopted.
        if options.get("trace") and run_id:
            written = await target.stop_trace(os.path.join(artifacts_dir, "trace.zip"))
            if written:
                storage.add_artifact(
                    run_id, "trace", os.path.relpath(written, artifacts_dir),
                    label="Playwright trace", size_bytes=os.path.getsize(written),
                )

    except asyncio.CancelledError:
        # Cut off: Stop's grace ran out (see `cancel`), or the server is going
        # down. Either way nobody decided this case.
        status = "cancelled"
        cut_off = True
        raise

    except Exception as exc:  # noqa: BLE001 — one bad case must not stop the suite
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        print(f"[suite] {label} crashed:\n{traceback.format_exc()}")

    finally:
        if target is not None:
            try:
                # Through the registry, so the session stops being offered for
                # watching at the same moment its browser goes away.
                await drivers.close(target.session_id)
                if options.get("record_video") and run_id:
                    await _attach_video(target, run_id)
            except Exception:
                pass
        _cleanup(staging)
        status = _stopped_or(status, last_status, control)
        if run_id:
            storage.finish_run(run_id, status, error, verdict_note=error,
                               blocked=was_blocked, store_keys=store_keys)
            _keep_what_worked(run_id, case, row)
            if status != "cancelled":
                _raise_bug_if_it_found_one(run_id, case, suite_run_id,
                                           options.get("suite_name"))
        else:
            run_id = _record_unstarted_case(
                case, suite_run_id, "web", error, row, label, status=status,
                store_keys=store_keys,
            )
        if cut_off:
            # Said here: nothing after this block runs for a case cut off, and
            # it used to vanish from the execution's totals.
            emit.put_nowait(_event("case_finished",
                                   **_case_result(case, run_id, label, status, error, started,
                                                  store_keys)))

    result = _case_result(case, run_id, label, status, error, started, store_keys)
    await emit.put(_event("case_finished", **result))
    return result


async def _run_mobile_case(
    execution: Dict[str, Any],
    target,
    suite_run_id: str,
    options: Dict[str, Any],
    emit: "asyncio.Queue[str]",
) -> Dict[str, Any]:
    """Run one case against the device that is already connected.

    A web case gets its own browser, which is what makes parallel runs safe.
    There is only one phone, so mobile cases share the open session and run one
    after another — the isolation a browser gives for free has to be bought
    here with sequence, and with the restart below.
    """
    case = execution["case"]
    row = execution["row"]
    label = execution["label"]
    # Read once per case, so a value edited between cases takes effect on the
    # next one rather than at the next restart.
    shared = storage.test_data_values()
    goal = with_precondition(
        substitute(case["goal"], row, shared) or case["goal"],
        substitute(case.get("precondition"), row, shared),
    )
    store_keys = storage.referenced_keys(
        case.get("goal"), case.get("precondition"), case.get("steps"))

    run_id: Optional[str] = None
    status = "failed"
    error: Optional[str] = None
    # Whether the site refused this run its data. Kept apart from the status
    # so a pass rate measures the product rather than the mood of its bot
    # protection — the first time the two were mixed, a critical bug was
    # raised against an airline for a button that was grey only because a
    # data call had been refused.
    was_blocked = False
    started = time.time()

    # Checked before anything is opened: a stopped execution must not spend a
    # browser, a model call or a row on the cases still queued behind it.
    control = options.get("control")
    if control is not None and control.stop.is_set():
        result = {
            "caseId": case["id"], "runId": None, "label": label,
            "status": "cancelled", "error": None, "durationMs": 0,
        }
        await emit.put(_event("case_finished", **result))
        return result

    await emit.put(_event("case_started", case=case["id"], label=label, url=None))

    last_status = None
    cut_off = False
    try:
        # A fresh, throwaway state per case: this call is nested inside whatever
        # is already driving `target` (a chat run invoking `run_test_set`, or a
        # plain execution). Reusing agent.get_session(target.session_id) here
        # would either collide with a run already marked active on that same
        # device — every case failing instantly, with no run ever created,
        # which is the bug this replaces — or, once past that, silently
        # overwrite the outer run's history/run_id/cancel mid-flight.
        # Handed to the control as well, because a state kept in no registry is
        # a state Stop cannot reach.
        case_state = agent.AgentSession()
        if control is not None:
            control.states.append(case_state)
        async for line in agent.run_agent(
            target, goal,
            max_steps=options.get("max_steps"),  # scaled to the steps — see the web path
            use_vision=options.get("use_vision", True),
            session_state=case_state,
            steps=substitute_steps(case.get("steps"), row, shared),
            store_keys=store_keys,
        ):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("runId") and not run_id:
                run_id = payload["runId"]
                storage.link_run_to_suite(
                    run_id, suite_run_id, case["id"],
                    title=label, tags=case.get("tags") or [],
                    dataset_row=row, kind="mobile",
                    priority=case.get("priority"), layer=case.get("layer"),
                    case_idx=case.get("idx"),
                )
            if payload.get("event") == "finished":
                last_status = payload.get("status")
            # A scenario the tester stopped is not one that failed. Without
            # this the `cancelled` event went unread, last_status stayed None
            # and the case fell through to "failed" — a stopped run reported
            # as a broken one.
            elif payload.get("event") == "cancelled":
                last_status = "cancelled"
                error = payload.get("message")
            elif payload.get("event") == "error":
                error = payload.get("message")
        status = last_status or "failed"
    except asyncio.CancelledError:
        # Cut off — see the web path.
        status = "cancelled"
        cut_off = True
        raise
    except Exception as exc:  # noqa: BLE001 — one bad case must not stop the suite
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        print(f"[suite] {label} crashed:\n{traceback.format_exc()}")
    finally:
        status = _stopped_or(status, last_status, control)
        if run_id:
            storage.finish_run(run_id, status, error, verdict_note=error,
                               blocked=was_blocked, store_keys=store_keys)
            _keep_what_worked(run_id, case, row)
            if status != "cancelled":
                _raise_bug_if_it_found_one(run_id, case, suite_run_id,
                                           options.get("suite_name"))
        else:
            run_id = _record_unstarted_case(
                case, suite_run_id, "mobile", error, row, label, status=status,
                store_keys=store_keys,
            )
        if cut_off:
            # Said here — see the web path.
            emit.put_nowait(_event("case_finished",
                                   **_case_result(case, run_id, label, status, error, started,
                                                  store_keys)))

    result = _case_result(case, run_id, label, status, error, started, store_keys)
    await emit.put(_event("case_finished", **result))
    return result


async def _attach_video(target: WebTarget, run_id: str) -> None:
    """Register the recorded video, which only exists once the page is closed.

    It was recorded into a staging folder chosen before the run had an id, so
    it is moved under the run here — the download endpoint refuses any path
    that resolves outside the run's own directory.
    """
    source = await target.video_path()
    if not source or not os.path.exists(source):
        return

    artifacts_dir = run_artifact_dir(run_id)
    destination_dir = os.path.join(artifacts_dir, "video")
    os.makedirs(destination_dir, exist_ok=True)
    destination = os.path.join(destination_dir, os.path.basename(source))

    try:
        shutil.move(source, destination)
    except Exception as exc:
        print(f"[suite] could not move the recording: {exc}")
        return

    storage.add_artifact(
        run_id, "video", os.path.relpath(destination, artifacts_dir),
        label="Session recording", size_bytes=os.path.getsize(destination),
    )


def _cleanup(directory: str) -> None:
    """Drop the staging folder once anything worth keeping has been moved."""
    try:
        if os.path.isdir(directory):
            shutil.rmtree(directory, ignore_errors=True)
    except Exception:
        pass


def _connected_device():
    """The open device session, if there is one.

    Reused rather than opened here on purpose: a second Appium session against
    the same phone collides on WebDriverAgent's port, and the device the tester
    is already looking at is the one they mean.
    """
    import drivers
    for target in drivers.all_targets().values():
        if getattr(target, "kind", None) == "mobile":
            return target
    return None


async def run_suite(
    suite_id: Optional[str] = None,
    workers: int = 1,
    tags: Optional[List[str]] = None,
    cases: Optional[List[Dict[str, Any]]] = None,
    name: Optional[str] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    **options: Any,
) -> AsyncGenerator[str, None]:
    """Execute an execution, yielding NDJSON progress as cases finish.

    Either a whole Test Set (`suite_id`, optionally narrowed by `tags`) or an
    explicit list of `cases` picked by hand, possibly from several sets.
    """
    whole_set = cases is None
    if cases is None:
        suite = storage.get_suite(suite_id)
        if suite is None:
            yield _event("error", message=f"No suite with id {suite_id}.")
            return
    else:
        # Assembled by hand: the kind comes from the cases themselves, and the
        # label from the sets they were drawn from.
        kinds = {c.get("suite_kind") or "web" for c in cases}
        # Only when every case agrees. A run mixing an iOS set with an Android
        # one belongs to neither phone, and claiming one would file it — and
        # its report — under a platform half of it never touched.
        phones = {c.get("suite_os") for c in cases if c.get("suite_os")}
        # One tab per execution, and the first scenario picked decides it: the
        # page offers one tab's sets at a time, so a mix only happens when a
        # selection is carried across tabs, and it is filed where it began.
        tracks = [c.get("suite_track") for c in cases if c.get("suite_track")]
        suite = {
            "id": suite_id,
            "name": name or " + ".join(
                dict.fromkeys(c.get("suite_name") or "Test Set" for c in cases)
            ),
            "kind": "mobile" if kinds == {"mobile"} else "web",
            "os": phones.pop() if len(phones) == 1 else None,
            "track": tracks[0] if tracks else None,
        }

    cases = cases if cases is not None else storage.select_cases(suite_id, tags)

    # A scenario still waiting on the data its precondition asked for is
    # dropped here as well as at selection, because a hand-picked execution and
    # the CLI reach this by other routes. Running one would fail on the missing
    # setup and the report would name the feature rather than the absent member
    # number — and it would do that on every run until someone noticed.
    waiting = [c for c in cases if c.get("needsData")]
    if waiting:
        cases = [c for c in cases if not c.get("needsData")]
        yield _event(
            "cases_skipped",
            reason="awaiting-precondition-data",
            count=len(waiting),
            cases=[{"id": c["id"], "name": c.get("name"), "missing": c.get("missingData") or []}
                   for c in waiting],
            message=(
                f"{len(waiting)} scenario(s) skipped: still waiting on their "
                "precondition data."
            ),
        )

    if not cases:
        if waiting:
            yield _event("error", message=(
                f"Every scenario in '{suite['name']}' is waiting on its precondition "
                "data. Fill it in on Test Sets and they turn on."
            ))
            return
        criteria = f" matching {', '.join(tags)}" if tags else ""
        yield _event("error", message=f"Suite '{suite['name']}' has no enabled cases{criteria}.")
        return

    # Before anything expands: a scenario that has never run can start from
    # what a sibling already learned, if they open on the same screen and
    # begin the same way. Lent in memory only — the run keeps what actually
    # worked, and a borrowed recording that does not work is dropped with the
    # step that failed on it.
    try:
        lent = storage.lend_recordings(cases)
        if lent:
            print(f"[suite] lent {lent} recorded step(s) from scenarios that "
                  f"already earned them")
    except Exception as exc:  # noqa: BLE001
        print(f"[suite] could not lend recordings: {exc}")

    executions = expand_cases(cases)

    # One phone, one session: a mobile suite cannot fan out the way a web suite
    # does, and pretending otherwise would have two runs fighting over the same
    # device. It runs sequentially against whatever device is connected.
    is_mobile = (suite.get("kind") or "web") == "mobile"
    device = None
    if is_mobile:
        # The tester can name the phone when they start the run. It has to be
        # one they already opened, so the app on it and the permissions it was
        # granted are the ones they picked — opening a second session against
        # the same device collides on WebDriverAgent's port.
        chosen = options.get("device_session_id")
        if chosen:
            candidate = drivers.get(chosen)
            if candidate is None or getattr(candidate, "kind", None) != "mobile":
                yield _event(
                    "error",
                    message=(
                        "Seçilen cihaz oturumu artık açık değil. Mobile ekranından "
                        "cihazı yeniden bağlayıp tekrar koşturun."
                    ),
                )
                return
            device = candidate
        else:
            device = _connected_device()
        if device is None:
            yield _event(
                "error",
                message=(
                    f"“{suite['name']}” bir mobil test seti ve şu an bağlı cihaz yok. "
                    "Mobile ekranından cihazı bağlayın, sonra tekrar koşturun."
                ),
            )
            return
        workers = 1

    workers = max(1, min(int(workers or 1), MAX_WORKERS))
    suite_run_id = storage.create_suite_run(
        suite_id, workers, name=name or suite.get("name"),
        sources=sources or (
            [{"suite_id": c.get("suite_id"), "suite_name": c.get("suite_name")} for c in cases]
            if cases else None
        ),
        kind=suite.get("kind") or "web",
        # Read off the set rather than the device: an execution assembled from
        # several sets still belongs to one phone OS, and the report has to say
        # which long after the session that ran it is gone.
        os=suite.get("os"),
        track=suite.get("track"),
    )

    # Carried down to each case so a watchable session can name the execution
    # it belongs to — the session list is all the workspace has to go on.
    execution_name = name or suite.get("name")
    control = _RunControl()
    _RUNNING[suite_run_id] = control
    # Stopped along with whatever asked for it: a chat run that started this
    # execution is waiting on it, not on a case, so that run's Stop has to be
    # passed on or it reaches nothing.
    stop_with: Optional[asyncio.Event] = options.pop("stop_with", None)
    follower: Optional["asyncio.Task[None]"] = None
    if stop_with is not None:
        async def follow() -> None:
            await stop_with.wait()
            await cancel(suite_run_id)
        follower = asyncio.create_task(follow())
    # The Test Set a bug is filed under. A case picked by hand carries its own
    # set's name; one read out of its set does not, so the set says it here.
    options = {**options, "execution_name": execution_name, "control": control,
               "suite_name": suite.get("name") if whole_set else None}

    yield _event(
        "suite_started", suiteRunId=suite_run_id, suite=suite["name"],
        cases=len(cases), executions=len(executions), workers=workers,
    )

    queue: "asyncio.Queue[str]" = asyncio.Queue()
    limiter = asyncio.Semaphore(workers)
    results: List[Dict[str, Any]] = []

    def never_started(execution: Dict[str, Any]) -> None:
        # Cut off before its turn came: counted as cancelled, the way a case
        # Stop reaches before it starts is (see `_execute_one`).
        queue.put_nowait(_event(
            "case_finished", caseId=execution["case"]["id"], runId=None,
            label=execution["label"], status="cancelled", error=None, durationMs=0,
        ))

    async def guarded(execution: Dict[str, Any]) -> Dict[str, Any]:
        began = False
        try:
            async with limiter:
                began = True
                return await _execute_one(execution, suite_run_id, options, queue)
        except asyncio.CancelledError:
            if not began:
                never_started(execution)
            raise

    if is_mobile:
        async def sequential():
            results = []
            # Read once, and never at the cost of the run: a device that will
            # not describe itself is a device the scenarios can still be driven
            # against, so the restart is skipped rather than the suite lost.
            try:
                described = device.describe() or {}
                # bundleId first: on a cloud session `appId` is the upload
                # handle the session was booked with, which names nothing on
                # the phone — terminating it failed silently and every mobile
                # set stayed order-dependent.
                # `appId` comes along as well: clearing on iOS means removing
                # the app and installing it again, and the upload handle is
                # the only thing there is to install back.
                restart = (described.get("bundleId") or described.get("appId"),
                           described.get("platform", ""),
                           device.session_id,
                           described.get("appId"))
            except Exception:  # noqa: BLE001
                restart = None

            for index, execution in enumerate(executions):
                # Between scenarios, not before the first: the session was just
                # opened on the app's own screen, and closing it again only to
                # reopen it would cost a restart for nothing. From the second
                # on it is what keeps one scenario's leftovers — a destination
                # (see below for the one case that does want it first)
                # still filled in, a panel left open — out of the next one.
                # A scenario whose opening state the app cannot be talked into
                # — an empty port field, a first-run prompt — says so with the
                # `fresh-app` tag, and gets the app's stored data cleared as
                # well as the app restarted. Asked for rather than done every
                # time, because it costs the onboarding walk and any saved
                # sign-in. Before the first case too, since the session was
                # opened on an app that has been used by whatever ran last.
                wants_clean = FRESH_APP in {
                    str(tag).strip().lower()
                    for tag in (execution["case"].get("tags") or [])
                }
                began = False
                try:
                    if (index or wants_clean) and restart and restart[0]:
                        app_id, platform_name, session_id, source = restart
                        await mobile_session.restart_app(
                            session_id, platform_name, app_id,
                            wipe=wants_clean, source=source)
                    began = True
                    results.append(
                        await _run_mobile_case(execution, device, suite_run_id, options, queue)
                    )
                except asyncio.CancelledError:
                    # Cut off after Stop. The case in hand says so itself once
                    # it has begun (see `_run_mobile_case`); the ones still to
                    # come never will.
                    for waiting in executions[index + (1 if began else 0):]:
                        never_started(waiting)
                    raise
            return results

        async def on_the_phone():
            # The phone is the execution's until its cases are done, however
            # they end. Held by the task rather than the stream: a closed tab
            # stops reading the stream while the cases carry on.
            session_id = device.session_id
            _CLAIMED[session_id] = {"suiteRunId": suite_run_id, "name": execution_name}
            try:
                return await sequential()
            finally:
                if (_CLAIMED.get(session_id) or {}).get("suiteRunId") == suite_run_id:
                    _CLAIMED.pop(session_id, None)

        tasks = [asyncio.create_task(on_the_phone())]
    else:
        tasks = [asyncio.create_task(guarded(execution)) for execution in executions]
    control.tasks = tasks
    gathered = asyncio.gather(*tasks, return_exceptions=True)

    # Drain the queue while the workers run so progress is live rather than
    # arriving in one lump when the last case finishes.
    #
    # The totals are read off the same lines. They used to come from what the
    # workers returned, and a case cut off after Stop returns nothing: it went
    # missing from the totals, and on a phone — one worker for every case —
    # so did every case that had already finished before it.
    while not gathered.done() or not queue.empty():
        try:
            line = await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        payload = json.loads(line)
        if payload.get("event") == "case_finished":
            results.append({key: value for key, value in payload.items() if key != "event"})
        yield line

    await gathered
    if follower is not None:
        follower.cancel()

    _RUNNING.pop(suite_run_id, None)

    passed = sum(1 for r in results if r["status"] == "passed")
    cancelled = sum(1 for r in results if r["status"] == "cancelled")
    failed = len(results) - passed - cancelled
    if control.stop.is_set():
        status = "cancelled"
    else:
        status = "passed" if failed == 0 and results else "failed"
    storage.finish_suite_run(
        suite_run_id, status,
        error="Stopped by the user." if status == "cancelled" else None,
    )

    yield _event(
        "suite_finished", suiteRunId=suite_run_id, status=status,
        total=len(results), passed=passed, failed=failed, cancelled=cancelled,
        results=results,
    )


async def run_suite_collect(
    suite_id: str, workers: int = 1, tags: Optional[List[str]] = None, **options: Any
) -> Dict[str, Any]:
    """Run a suite to completion and return the summary — the shape the CLI wants."""
    summary: Dict[str, Any] = {"status": "failed", "results": []}
    async for line in run_suite(suite_id, workers, tags, **options):
        payload = json.loads(line)
        if payload.get("event") == "suite_finished":
            summary = payload
        elif payload.get("event") == "error":
            summary = {"status": "error", "message": payload.get("message"), "results": []}
        elif payload.get("event") == "case_finished":
            print(
                f"  {'PASS' if payload['status'] == 'passed' else 'FAIL'}  "
                f"{payload['label']}  ({payload['durationMs'] / 1000:.1f}s)"
            )
            if payload.get("error"):
                print(f"        {payload['error']}")
    return summary
