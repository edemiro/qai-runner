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
import config
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


# The verbs a tester wraps around the thing they actually want scenarios for.
# "tek yön uçuş ara ekranı senaryolarını yaz" names a set called "Tek yön uçuş
# ara ekranı" — the instruction to write is not part of the name.
_ASK_VERBS = re.compile(
    r"\b(test\s+)?(senaryolar[ıi]n[ıi]|senaryolar[ıi]|senaryo(su)?|scenarios?)\s*"
    r"(yaz(ar\s+m[ıi]s[ıi]n)?|ç[ıi]kar(t)?([ıi]r\s+m[ıi]s[ıi]n)?|olu[şs]tur(ur\s+m[ıi]s[ıi]n)?"
    r"|üret(ir\s+m[ıi]s[ıi]n)?|write|generate|create)\b.*$"
    r"|\b(yaz(ar\s+m[ıi]s[ıi]n)?|ç[ıi]kar(t)?([ıi]r\s+m[ıi]s[ıi]n)?|olu[şs]tur|üret)\s*$"
    r"|^\s*(bu\s+ekran(ın|in)?|bu\s+sayfan[ıi]n|for\s+this\s+screen)\s*",
    re.IGNORECASE,
)

# "ekranının senaryoları" leaves the screen in the genitive once the verb is
# gone; the set is called "…ekranı", not "…ekranının".
_GENITIVE = re.compile(r"(?<=[ıiuü])(n[ıiuü]n)$", re.IGNORECASE)


def _name_from_brief(brief: Optional[str]) -> str:
    """The set name a tester meant, read off what they asked for.

    Preferred over the screen's own label: two requests about the same page
    ("uçuş ara ekranı", then "tek yön uçuş ara ekranı") are two different sets,
    and only the brief tells them apart.
    """
    text = " ".join((brief or "").split())
    if not text:
        return ""
    # English puts the verb first ("write scenarios for the search screen");
    # Turkish puts it last, which _ASK_VERBS handles. Strip both shapes.
    text = re.sub(
        r"^(please\s+)?(write|generate|create|produce)\s+(the\s+)?(test\s+)?scenarios?\s+(for\s+)?(this\s+|the\s+)?",
        "", text, flags=re.IGNORECASE,
    )
    text = _ASK_VERBS.sub("", text).strip(" -–:,.")
    # A connector left dangling once the verb is gone: "uçuş ara ekranı için".
    text = re.sub(r"\s+(için|for|about|on)$", "", text, flags=re.IGNORECASE)
    text = _GENITIVE.sub("", text)
    text = " ".join(text.split())[:60]
    return text[:1].upper() + text[1:] if text else ""


async def write_scenarios(
    *,
    name: str,
    tree: Optional[Dict[str, Any]],
    screenshot: Optional[str],
    kind: str,
    url: Optional[str] = None,
    brief: Optional[str] = None,
    asked: Optional[str] = None,
) -> Dict[str, Any]:
    """Write scenarios for the screen in front of the agent — as a proposal.

    Nothing is saved here. The scenarios, and the Test Set name they seem to be
    for, go back to the chat so the tester can tick, edit and name them before
    they land; a set of scenarios that files itself gets copied by the next
    person, mistakes included. Saving and running happen from that review.
    """
    if tree is None:
        return {"ok": False, "message": "The current screen could not be read, so there is nothing to write scenarios from."}

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

    # The tester's own words first, then whatever the model chose, then the
    # screen — the last of these is the "Turkish Airlines" label that makes
    # every set from the same site indistinguishable.
    suggested = (
        _name_from_brief(asked)
        or _name_from_brief(brief)
        or " ".join((name or "").split())
        or _name_from_screen(tree, url)
    )
    count = len(result["scenarios"])
    return {
        "ok": True,
        "proposed": True,
        "scenarios": result["scenarios"],
        "suggestedName": suggested,
        "readFrom": url,
        "kind": "web" if kind == "web" else "mobile",
        "message": (
            f"{count} senaryo yazıldı ve incelemene açıldı — "
            f"onayla, düzenle, set adını ver ve kaydet (veya kaydedip koştur)."
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
    # .get with a fallback: every case read from storage carries these, but a
    # caller that hands in a case it built itself should not crash on a field
    # that is derived rather than stored.
    runnable = [c for c in detail["cases"] if c.get("runnable", c.get("enabled"))]
    if not runnable:
        waiting = [c for c in detail["cases"] if c.get("needsData")]
        if waiting:
            return {
                "ok": False,
                "message": (
                    f"Test Set “{suite['name']}” has {len(waiting)} scenario(s) waiting on "
                    "their precondition data — fill that in on Test Sets and they turn on."
                ),
            }
        return {"ok": False, "message": f"Test Set “{suite['name']}” has no enabled scenario to run."}

    # Graded exactly as the same set is from the Test Sets page. It used to
    # pass fail_on_page_error=False here, so a set run from the chat and the
    # same set run from a button could reach different verdicts on the same
    # app — and the chat was the lenient one, which is the wrong way round for
    # the path a tester trusts without opening the report.
    summary = await suite_runner.run_suite_collect(
        suite["id"], workers=workers, name=execution_name,
        headless=config.RUN_HEADLESS_DEFAULT,
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
