"""Turn a run — or a whole suite run — into the report formats CI understands.

JUnit XML is the lingua franca: Jenkins, GitHub Actions, GitLab and TeamCity
all parse it natively and will render per-test results without any plugin. The
JSON report carries everything JUnit has no place for — healed steps, page
errors, artifacts, visual diffs — for anything that wants the detail.
"""

import json
import time
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import storage


def _escape_control_chars(text: Optional[str]) -> str:
    """XML 1.0 cannot carry most control characters at all, and a stray one in
    a page's error text makes the whole report unparseable."""
    if not text:
        return ""
    return "".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 0x20)


def _failure_text(run: Dict[str, Any]) -> str:
    """Everything a developer needs to see the failure without opening QAi."""
    lines: List[str] = []
    if run.get("error"):
        lines.append(f"Error: {run['error']}")
    # Often the same sentence, since a page-error verdict writes both; printing
    # it twice makes the report look duplicated rather than thorough.
    if run.get("verdict_note") and run.get("verdict_note") != run.get("error"):
        lines.append(run["verdict_note"])

    for step in run.get("steps", []):
        if step.get("status") == "failed":
            target = f" [{step['target']}]" if step.get("target") else ""
            lines.append(f"Step {step['idx']} {step['action']}{target}: {step.get('message') or ''}")

    errors = [e for e in run.get("pageEvents", []) if e.get("level") == "error"]
    if errors:
        lines.append("")
        lines.append(f"Page errors ({len(errors)}):")
        for event in errors[:10]:
            location = f" {event['url']}" if event.get("url") else ""
            lines.append(f"  {event['kind']}:{location} {event.get('text') or ''}")

    return _escape_control_chars("\n".join(lines) or "The run failed without a recorded reason.")


def _system_out(run: Dict[str, Any]) -> str:
    lines = [f"goal: {run.get('goal', '')}"]
    for step in run.get("steps", []):
        mark = "PASS" if step.get("status") == "passed" else "FAIL"
        healed = " (healed)" if step.get("healed") else ""
        lines.append(f"  {mark} {step['idx']}. {step['action']}{healed}: {step.get('message') or ''}")
    for artifact in run.get("artifacts", []):
        lines.append(f"  artifact[{artifact['kind']}]: {artifact['path']}")
    return _escape_control_chars("\n".join(lines))


def junit_xml(
    runs: List[Dict[str, Any]],
    suite_name: str = "QAi",
    hostname: str = "qai",
) -> str:
    """One <testsuite> holding one <testcase> per run.

    A run with no assertion, or one that logged page errors while otherwise
    passing, is reported as a failure — the whole point of recording those
    signals is that they change the verdict.
    """
    total_time = 0.0
    failures = 0
    errors = 0
    skipped = 0

    suite_element = ET.Element("testsuite")
    for run in runs:
        seconds = (run.get("duration_ms") or 0) / 1000.0
        total_time += seconds

        case = ET.SubElement(suite_element, "testcase")
        # Attribute values need the same scrub as element text: a control
        # character in a run title makes the whole report unparseable, and a
        # title is user-supplied text that has been through a page and a model.
        case.set("name", _escape_control_chars(
            run.get("title") or run.get("goal", "")[:80] or "run"
        ))
        case.set("classname", _escape_control_chars(
            f"{suite_name}.{run.get('kind') or run.get('platform') or 'qai'}"
        ))
        case.set("time", f"{seconds:.3f}")
        case.set("id", run.get("id", ""))

        status = run.get("status")
        if status == "failed":
            failures += 1
            failure = ET.SubElement(case, "failure")
            failure.set("message", _escape_control_chars(
                (run.get("error") or run.get("verdict_note") or "Run failed")[:200]
            ))
            failure.set("type", "AssertionError")
            failure.text = _failure_text(run)
        elif status in ("error", "cancelled"):
            errors += 1
            error = ET.SubElement(case, "error")
            error.set("message", _escape_control_chars((run.get("error") or status)[:200]))
            error.text = _failure_text(run)
        elif status == "running":
            skipped += 1
            ET.SubElement(case, "skipped").set("message", "The run never finished.")

        out = ET.SubElement(case, "system-out")
        out.text = _system_out(run)

    suite_element.set("name", _escape_control_chars(suite_name))
    suite_element.set("tests", str(len(runs)))
    suite_element.set("failures", str(failures))
    suite_element.set("errors", str(errors))
    suite_element.set("skipped", str(skipped))
    suite_element.set("time", f"{total_time:.3f}")
    suite_element.set("hostname", hostname)
    suite_element.set("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))

    suites = ET.Element("testsuites")
    suites.set("name", _escape_control_chars(suite_name))
    suites.set("tests", str(len(runs)))
    suites.set("failures", str(failures))
    suites.set("errors", str(errors))
    suites.set("time", f"{total_time:.3f}")
    suites.append(suite_element)

    ET.indent(suites, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suites, encoding="unicode")


def json_report(runs: List[Dict[str, Any]], suite_name: str = "QAi") -> Dict[str, Any]:
    """The full detail, for anything that wants more than pass/fail."""
    cases = []
    for run in runs:
        page_errors = [e for e in run.get("pageEvents", []) if e.get("level") == "error"]
        cases.append({
            "id": run.get("id"),
            "title": run.get("title"),
            "goal": run.get("goal"),
            "status": run.get("status"),
            "kind": run.get("kind"),
            "tags": run.get("tags", []),
            "caseId": run.get("case_id"),
            "datasetRow": run.get("dataset_row"),
            "durationMs": run.get("duration_ms"),
            "error": run.get("error"),
            "verdictNote": run.get("verdict_note"),
            "stepCount": run.get("step_count"),
            "failedCount": run.get("failed_count"),
            "healedCount": run.get("healed_count", 0),
            "pageErrorCount": len(page_errors),
            "steps": [
                {
                    "idx": step["idx"], "action": step["action"], "target": step.get("target"),
                    "value": step.get("value"), "status": step["status"],
                    "message": step.get("message"), "healed": step.get("healed", False),
                    "durationMs": step.get("duration_ms"),
                    "hasScreenshot": step.get("hasScreenshot", False),
                }
                for step in run.get("steps", [])
            ],
            "pageEvents": [
                {
                    "kind": e["kind"], "level": e.get("level"), "text": e.get("text"),
                    "url": e.get("url"), "status": e.get("status"),
                }
                for e in run.get("pageEvents", [])
            ],
            "artifacts": [
                {"kind": a["kind"], "label": a.get("label"), "path": a["path"],
                 "sizeBytes": a.get("size_bytes")}
                for a in run.get("artifacts", [])
            ],
        })

    passed = sum(1 for c in cases if c["status"] == "passed")
    failed = sum(1 for c in cases if c["status"] == "failed")
    return {
        "tool": "QAi",
        "suite": suite_name,
        "generatedAt": time.time(),
        "summary": {
            "total": len(cases),
            "passed": passed,
            "failed": failed,
            "other": len(cases) - passed - failed,
            "durationMs": sum(c["durationMs"] or 0 for c in cases),
            "healed": sum(c["healedCount"] for c in cases),
            "pageErrors": sum(c["pageErrorCount"] for c in cases),
        },
        "cases": cases,
    }


def report_for_suite_run(suite_run_id: str) -> Optional[Dict[str, Any]]:
    """Load a suite run with every case fully hydrated, ready to report on."""
    suite_run = storage.get_suite_run(suite_run_id)
    if suite_run is None:
        return None
    suite_run["runs"] = [
        storage.get_run(run["id"]) or run for run in suite_run.get("runs", [])
    ]
    return suite_run


def write_reports(
    runs: List[Dict[str, Any]],
    suite_name: str,
    junit_path: Optional[str] = None,
    json_path: Optional[str] = None,
) -> Dict[str, str]:
    written = {}
    if junit_path:
        with open(junit_path, "w", encoding="utf-8") as handle:
            handle.write(junit_xml(runs, suite_name))
        written["junit"] = junit_path
    if json_path:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(json_report(runs, suite_name), handle, ensure_ascii=False, indent=2)
        written["json"] = json_path
    return written
