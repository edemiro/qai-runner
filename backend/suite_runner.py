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
import json
import os
import re
import shutil
import time
import traceback
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

import agent
import drivers
import storage
import config
from drivers.web import WebTarget, run_artifact_dir

# More than this and the machine, not the site, becomes the bottleneck — each
# worker is a full browser with its own renderer processes.
MAX_WORKERS = 8

PLACEHOLDER = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")


def substitute(text: Optional[str], row: Optional[Dict[str, Any]]) -> Optional[str]:
    """Fill {{placeholders}} from a dataset row.

    An unknown placeholder is left as written rather than blanked, so a typo
    shows up in the report as `{{emial}}` instead of silently becoming "".
    """
    if not text or not row:
        return text
    return PLACEHOLDER.sub(
        lambda match: str(row.get(match.group(1), match.group(0))), text
    )


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


def _event(kind: str, **payload) -> str:
    return json.dumps({"event": kind, **payload}, ensure_ascii=False) + "\n"


def _record_unstarted_case(
    case: Dict[str, Any],
    suite_run_id: str,
    kind: str,
    error: Optional[str],
    row: Optional[Dict[str, Any]],
    label: str,
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
    )
    storage.link_run_to_suite(
        run_id, suite_run_id, case["id"],
        title=label, tags=case.get("tags") or [],
        dataset_row=row, kind=kind,
        priority=case.get("priority"), layer=case.get("layer"),
        case_idx=case.get("idx"),
    )
    reason = error or "The run never started and gave no reason."
    storage.finish_run(run_id, "failed", reason, verdict_note=reason)
    return run_id


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

    goal = substitute(case["goal"], row) or case["goal"]
    url = substitute(case.get("url") or options.get("base_url"), row)

    run_id: Optional[str] = None
    target: Optional[WebTarget] = None
    status = "failed"
    error: Optional[str] = None
    started = time.time()

    await emit.put(_event("case_started", case=case["id"], label=label, url=url))

    # The video directory has to be chosen before the browser starts, but the
    # run id only exists once the agent has created it — so recordings go under
    # the case, and are moved into the run's folder afterwards. Assigned before
    # the try because `finally` cleans it up: raising for a missing URL inside
    # the try left it unbound, and the UnboundLocalError that followed escaped
    # `_execute_one` entirely — gather() swallowed it, the case produced no
    # `case_finished` and no result row, and a suite whose other cases passed
    # was reported green while silently dropping that one.
    staging = run_artifact_dir(f"case-{case['id']}-{uuid4().hex[:8]}")

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

        last_status = None
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
            steps=case.get("steps") or None,
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
            elif payload.get("event") == "error":
                error = payload.get("message")

        status = last_status or "failed"

        artifacts_dir = run_artifact_dir(run_id) if run_id else staging
        events = target.drain_events()
        if run_id:
            storage.add_page_events(run_id, events)

        page_errors = [e for e in events if e.get("level") == "error"]
        if status == "passed" and page_errors and options.get("fail_on_page_error", True):
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
        if run_id:
            storage.finish_run(run_id, status, error, verdict_note=error)
        else:
            run_id = _record_unstarted_case(
                case, suite_run_id, "web", error, row, label,
            )

    result = {
        "caseId": case["id"], "runId": run_id, "label": label,
        "status": status, "error": error,
        "durationMs": int((time.time() - started) * 1000),
    }
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
    here with sequence.
    """
    case = execution["case"]
    row = execution["row"]
    label = execution["label"]
    goal = substitute(case["goal"], row) or case["goal"]

    run_id: Optional[str] = None
    status = "failed"
    error: Optional[str] = None
    started = time.time()

    await emit.put(_event("case_started", case=case["id"], label=label, url=None))

    try:
        last_status = None
        # A fresh, throwaway state per case: this call is nested inside whatever
        # is already driving `target` (a chat run invoking `run_test_set`, or a
        # plain execution). Reusing agent.get_session(target.session_id) here
        # would either collide with a run already marked active on that same
        # device — every case failing instantly, with no run ever created,
        # which is the bug this replaces — or, once past that, silently
        # overwrite the outer run's history/run_id/cancel mid-flight.
        async for line in agent.run_agent(
            target, goal,
            max_steps=options.get("max_steps"),  # scaled to the steps — see the web path
            use_vision=options.get("use_vision", True),
            session_state=agent.AgentSession(),
            steps=case.get("steps") or None,
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
            elif payload.get("event") == "error":
                error = payload.get("message")
        status = last_status or "failed"
    except Exception as exc:  # noqa: BLE001 — one bad case must not stop the suite
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        print(f"[suite] {label} crashed:\n{traceback.format_exc()}")
    finally:
        if run_id:
            storage.finish_run(run_id, status, error, verdict_note=error)
        else:
            run_id = _record_unstarted_case(
                case, suite_run_id, "mobile", error, row, label,
            )

    result = {
        "caseId": case["id"], "runId": run_id, "label": label,
        "status": status, "error": error,
        "durationMs": int((time.time() - started) * 1000),
    }
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
    if cases is None:
        suite = storage.get_suite(suite_id)
        if suite is None:
            yield _event("error", message=f"No suite with id {suite_id}.")
            return
    else:
        # Assembled by hand: the kind comes from the cases themselves, and the
        # label from the sets they were drawn from.
        kinds = {c.get("suite_kind") or "web" for c in cases}
        suite = {
            "id": suite_id,
            "name": name or " + ".join(
                dict.fromkeys(c.get("suite_name") or "Test Set" for c in cases)
            ),
            "kind": "mobile" if kinds == {"mobile"} else "web",
        }

    cases = cases if cases is not None else storage.select_cases(suite_id, tags)
    if not cases:
        criteria = f" matching {', '.join(tags)}" if tags else ""
        yield _event("error", message=f"Suite '{suite['name']}' has no enabled cases{criteria}.")
        return

    executions = expand_cases(cases)

    # One phone, one session: a mobile suite cannot fan out the way a web suite
    # does, and pretending otherwise would have two runs fighting over the same
    # device. It runs sequentially against whatever device is connected.
    is_mobile = (suite.get("kind") or "web") == "mobile"
    device = None
    if is_mobile:
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
    )

    # Carried down to each case so a watchable session can name the execution
    # it belongs to — the session list is all the workspace has to go on.
    execution_name = name or suite.get("name")
    options = {**options, "execution_name": execution_name}

    yield _event(
        "suite_started", suiteRunId=suite_run_id, suite=suite["name"],
        cases=len(cases), executions=len(executions), workers=workers,
    )

    queue: "asyncio.Queue[str]" = asyncio.Queue()
    limiter = asyncio.Semaphore(workers)
    results: List[Dict[str, Any]] = []

    async def guarded(execution: Dict[str, Any]) -> Dict[str, Any]:
        async with limiter:
            return await _execute_one(execution, suite_run_id, options, queue)

    if is_mobile:
        async def sequential():
            results = []
            for execution in executions:
                results.append(
                    await _run_mobile_case(execution, device, suite_run_id, options, queue)
                )
            return results

        tasks = [asyncio.create_task(sequential())]
    else:
        tasks = [asyncio.create_task(guarded(execution)) for execution in executions]
    gathered = asyncio.gather(*tasks, return_exceptions=True)

    # Drain the queue while the workers run so progress is live rather than
    # arriving in one lump when the last case finishes.
    while not gathered.done() or not queue.empty():
        try:
            yield await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue

    # A web run produces one task per case; the sequential mobile run produces
    # one task holding every case. Both shapes are flattened here so a mobile
    # suite does not silently report nothing.
    for outcome in await gathered:
        if isinstance(outcome, dict):
            results.append(outcome)
        elif isinstance(outcome, list):
            results.extend(item for item in outcome if isinstance(item, dict))

    passed = sum(1 for r in results if r["status"] == "passed")
    failed = len(results) - passed
    status = "passed" if failed == 0 and results else "failed"
    storage.finish_suite_run(suite_run_id, status)

    yield _event(
        "suite_finished", suiteRunId=suite_run_id, status=status,
        total=len(results), passed=passed, failed=failed,
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
