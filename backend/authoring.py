"""What the agent can do to QAi itself, rather than to the screen.

The agent's other actions all push a pixel: click, type, scroll. These two do
not — they write scenarios into a Test Set and run one. They live here rather
than in agent.py because they are a different kind of thing, and because the
agent loop should stay a loop about the screen.

The reason they exist at all: a tester describing work in the chat does not
think in surfaces. "Write the scenarios for this screen, add them to the Test
Set, then run it" is one sentence and three tools, and making the person go and
find each tool is making them do the routing.
"""

import re
from typing import Any, Dict, List, Optional

import scenario_writer
import storage
import suite_runner


def _match_suite(name: str) -> Optional[Dict[str, Any]]:
    """Find a Test Set the way a person names one — loosely, case-insensitively."""
    wanted = " ".join((name or "").split()).lower()
    if not wanted:
        return None
    suites = storage.list_suites()
    for suite in suites:
        if suite["name"].lower() == wanted:
            return suite
    for suite in suites:
        if wanted in suite["name"].lower() or suite["name"].lower() in wanted:
            return suite
    return None


def _name_from_screen(tree: Optional[Dict[str, Any]], url: Optional[str]) -> str:
    """A name that says what is in the set, when the tester did not give one.

    "Chat Test Set" says only where it was made, which is the least useful fact
    about it — three of them and nobody can tell which is which. The app or host
    name is on the screen already, so use that.
    """
    label = ""
    if isinstance(tree, dict):
        label = (tree.get("text") or tree.get("content-desc") or "").strip()
    if not label and url:
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        label = host or url
    label = " ".join(label.split())[:60]
    return label or "Untitled Test Set"


async def write_scenarios(
    *,
    name: str,
    tree: Optional[Dict[str, Any]],
    screenshot: Optional[str],
    kind: str,
    url: Optional[str] = None,
    brief: Optional[str] = None,
) -> Dict[str, Any]:
    """Write scenarios for the screen in front of the agent into a Test Set.

    The Test Set is created when it does not exist: the tester naming one that
    is not there means "put them here", not "fail".
    """
    if tree is None:
        return {"ok": False, "message": "The current screen could not be read, so there is nothing to write scenarios from."}

    target_name = " ".join((name or "").split()) or _name_from_screen(tree, url)
    suite = _match_suite(target_name)
    created = False
    if suite is None:
        suite_id = storage.create_suite(
            name=target_name,
            description="Created from the agent chat.",
            kind="web" if kind == "web" else "mobile",
            tags=[],
        )
        suite = storage.get_suite(suite_id)
        created = True

    result = await scenario_writer.generate(
        kind=kind, brief=brief, tree=tree, url=url, screenshot=screenshot,
    )

    if result["questions"]:
        # The model declining to guess is worth passing back verbatim: the
        # tester can answer in the next chat message.
        return {
            "ok": False,
            "message": "Bu senaryoları yazmadan önce şunlar netleşmeli: "
                       + " ".join(result["questions"]),
            "questions": result["questions"],
        }

    if not result["scenarios"]:
        return {"ok": False, "message": "No scenario came back in the required format."}

    for scenario in result["scenarios"]:
        storage.add_case(
            suite["id"], scenario["title"], scenario["goal"], url=url,
            priority=scenario["priority"], layer=scenario["layer"],
        )

    written = len(result["scenarios"])
    return {
        "ok": True,
        "suiteId": suite["id"],
        "suiteName": suite["name"],
        "written": written,
        "message": (
            f"Wrote {written} scenario{'s' if written != 1 else ''} to "
            f"{'the new Test Set' if created else 'Test Set'} “{suite['name']}”."
        ),
    }


def _failure_summary(results: list) -> str:
    """The distinct reasons behind the failures, not just their count.

    "12 failed" tells the tester nothing they can act on. Most real failures
    cluster into one or two causes (no credit, a selector that stopped
    matching, a device that dropped) — naming those is the difference between
    a report and a mystery.
    """
    reasons: Dict[str, int] = {}
    for r in results:
        if r.get("status") != "failed":
            continue
        reason = (r.get("error") or "no error recorded").strip()
        reasons[reason] = reasons.get(reason, 0) + 1
    if not reasons:
        return ""
    ranked = sorted(reasons.items(), key=lambda pair: -pair[1])[:3]
    lines = [f"  • ({count}x) {reason[:180]}" for reason, count in ranked]
    return "\nLikely cause" + ("s" if len(ranked) > 1 else "") + ":\n" + "\n".join(lines)


async def run_test_set(
    *, name: str, execution_name: Optional[str] = None, workers: int = 2,
) -> Dict[str, Any]:
    """Create an execution for a Test Set and run it.

    `execution_name` is the label the tester gave the execution itself, distinct
    from the Test Set it draws from — a Test Set can be run many times, and
    "Regression" reads a lot better in the Test Executions list than the Test
    Set's own name repeated over and over. Left blank, one is generated.

    A mobile suite shares the one connected device and runs sequentially; the
    runner enforces that itself and says so if no device is connected.
    """
    suite = _match_suite(name)
    if suite is None:
        known = ", ".join(f"“{s['name']}”" for s in storage.list_suites()) or "none yet"
        return {"ok": False, "message": f"No Test Set called “{name}”. Existing: {known}."}

    detail = storage.get_suite(suite["id"])
    runnable = [case for case in detail["cases"] if case["enabled"]]
    if not runnable:
        return {"ok": False, "message": f"Test Set “{suite['name']}” has no enabled scenario to run."}

    summary = await suite_runner.run_suite_collect(
        suite["id"], workers=workers, name=execution_name,
        headless=True, fail_on_page_error=False,
    )

    if summary.get("status") == "error":
        return {"ok": False, "message": summary.get("message") or "The execution could not start."}

    passed, failed, total = (
        summary.get("passed", 0), summary.get("failed", 0), summary.get("total", 0),
    )
    message = f"Ran “{suite['name']}”: {passed} passed, {failed} failed of {total}."
    if failed:
        message += _failure_summary(summary.get("results") or [])

    return {
        "ok": passed > 0 or failed == 0,
        "suiteRunId": summary.get("suiteRunId"),
        "message": message,
    }
