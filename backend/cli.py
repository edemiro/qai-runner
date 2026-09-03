"""QAi from the command line — the entry point CI actually calls.

Until this existed, QAi could only be driven from its own UI, which meant it
could not gate a pull request. Everything here is deliberately CI-shaped: no
prompts, no browser windows by default, machine-readable reports, and an exit
code that means what a build server expects it to mean.

    python -m cli run --suite <id> --tag smoke --workers 4 \
        --junit results.xml --json report.json

Exit codes:
    0  every case passed
    1  at least one case failed
    2  the run could not start (bad arguments, missing suite, no API key)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import reporters
import storage
import suite_runner

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2


def _print_summary(summary: Dict[str, Any], elapsed: float) -> None:
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    print()
    print(f"  {passed} passed, {failed} failed, {total} total  ({elapsed:.1f}s)")
    if failed:
        print()
        for result in summary.get("results", []):
            if result.get("status") != "passed":
                print(f"  FAILED  {result['label']}")
                if result.get("error"):
                    print(f"          {result['error']}")


def _hydrate(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Load each run in full, so the reports carry steps and page errors."""
    runs = []
    for result in summary.get("results", []):
        if not result.get("runId"):
            continue
        run = storage.get_run(result["runId"])
        if run:
            runs.append(run)
    return runs


async def _cmd_run(args: argparse.Namespace) -> int:
    suite = storage.get_suite(args.suite)
    if suite is None:
        print(f"error: no suite with id '{args.suite}'.", file=sys.stderr)
        print("       run `python -m cli suites` to list them.", file=sys.stderr)
        return EXIT_ERROR

    tags = [t.strip() for t in (args.tag or [])]
    print(f"QAi — suite '{suite['name']}'" + (f", tags: {', '.join(tags)}" if tags else ""))
    print(f"      {args.workers} worker(s), {'headed' if args.headed else 'headless'}")
    print()

    started = time.time()
    summary = await suite_runner.run_suite_collect(
        args.suite,
        workers=args.workers,
        tags=tags or None,
        headless=not args.headed,
        base_url=args.base_url,
        auth_profile=args.auth,
        record_video=args.video,
        trace=args.trace,
        width=args.width,
        height=args.height,
        max_steps=args.max_steps,
        use_vision=not args.no_vision,
        fail_on_page_error=not args.allow_page_errors,
    )

    if summary.get("status") == "error":
        print(f"error: {summary.get('message')}", file=sys.stderr)
        return EXIT_ERROR

    elapsed = time.time() - started
    _print_summary(summary, elapsed)

    runs = _hydrate(summary)
    written = reporters.write_reports(runs, suite["name"], args.junit, args.json)
    for kind, path in written.items():
        print(f"  {kind} report: {os.path.abspath(path)}")

    return EXIT_OK if summary.get("failed", 1) == 0 else EXIT_FAILED


def _cmd_suites(args: argparse.Namespace) -> int:
    suites = storage.list_suites()
    if not suites:
        print("No suites yet. Create one in the QAi UI, or with `cli create-suite`.")
        return EXIT_OK
    if args.json:
        print(json.dumps(suites, ensure_ascii=False, indent=2, default=str))
        return EXIT_OK
    width = max((len(s["name"]) for s in suites), default=4)
    print(f"{'ID':<18}{'NAME':<{width + 2}}{'CASES':>6}  TAGS")
    for suite in suites:
        print(
            f"{suite['id']:<18}{suite['name']:<{width + 2}}{suite['case_count']:>6}  "
            f"{', '.join(suite['tags'])}"
        )
    return EXIT_OK


def _cmd_cases(args: argparse.Namespace) -> int:
    suite = storage.get_suite(args.suite)
    if suite is None:
        print(f"error: no suite with id '{args.suite}'.", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps(suite["cases"], ensure_ascii=False, indent=2, default=str))
        return EXIT_OK
    print(f"{suite['name']} — {len(suite['cases'])} case(s)")
    for case in suite["cases"]:
        mark = " " if case["enabled"] else "-"
        rows = f" x{len(case['dataset'])}" if case.get("dataset") else ""
        print(f" {mark} {case['id']:<18}{case['name']}{rows}  {', '.join(case['tags'])}")
    return EXIT_OK


def _cmd_create_suite(args: argparse.Namespace) -> int:
    suite_id = storage.create_suite(
        args.name, description=args.description,
        tags=[t.strip() for t in (args.tag or [])],
    )
    print(suite_id)
    return EXIT_OK


def _cmd_add_case(args: argparse.Namespace) -> int:
    if storage.get_suite(args.suite) is None:
        print(f"error: no suite with id '{args.suite}'.", file=sys.stderr)
        return EXIT_ERROR

    dataset = None
    if args.dataset:
        dataset = _load_dataset(args.dataset)
        if dataset is None:
            return EXIT_ERROR

    case_id = storage.add_case(
        args.suite, args.name, args.goal, url=args.url,
        tags=[t.strip() for t in (args.tag or [])],
        dataset=dataset, auth_profile=args.auth,
    )
    print(case_id)
    return EXIT_OK


def _load_dataset(path: str) -> Optional[List[Dict[str, Any]]]:
    """Read rows from JSON or CSV — whichever the team already has."""
    if not os.path.exists(path):
        print(f"error: dataset '{path}' not found.", file=sys.stderr)
        return None
    try:
        if path.lower().endswith(".csv"):
            import csv
            with open(path, newline="", encoding="utf-8-sig") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            data = data.get("rows", [])
        if not isinstance(data, list):
            print("error: a dataset must be a list of rows.", file=sys.stderr)
            return None
        return [row for row in data if isinstance(row, dict)]
    except Exception as exc:
        print(f"error: could not read '{path}': {exc}", file=sys.stderr)
        return None


def _cmd_report(args: argparse.Namespace) -> int:
    suite_run = reporters.report_for_suite_run(args.suite_run)
    if suite_run is None:
        print(f"error: no suite run with id '{args.suite_run}'.", file=sys.stderr)
        return EXIT_ERROR
    runs = suite_run["runs"]
    name = suite_run.get("suite_name") or "QAi"
    if args.junit or args.json:
        written = reporters.write_reports(runs, name, args.junit, args.json)
        for kind, path in written.items():
            print(f"{kind}: {os.path.abspath(path)}")
    else:
        print(reporters.junit_xml(runs, name))
    return EXIT_OK if suite_run.get("failed", 0) == 0 else EXIT_FAILED


def _cmd_flaky(args: argparse.Namespace) -> int:
    report = storage.flakiness_report(limit=args.limit)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not report:
        print("Not enough history yet — a case needs at least two finished runs.")
        return EXIT_OK
    print(f"{'CASE':<34}{'RUNS':>5}{'PASS%':>7}{'FLIPS':>7}  SUITE")
    for row in report:
        print(
            f"{row['name'][:32]:<34}{row['runs']:>5}{row['pass_rate'] * 100:>6.0f}%"
            f"{row['flips']:>7}  {row['suite_name'] or ''}"
        )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qai", description="QAi — AI-driven QA automation, from the command line."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run a suite and write CI reports.")
    run.add_argument("--suite", required=True, help="Suite id (see `cli suites`).")
    run.add_argument("--tag", action="append", help="Only cases with this tag. Repeatable.")
    run.add_argument("--workers", type=int, default=1, help="How many cases to run at once.")
    run.add_argument("--headed", action="store_true", help="Show the browser (default: headless).")
    run.add_argument("--base-url", help="URL for cases that do not carry their own.")
    run.add_argument("--auth", help="Saved sign-in profile to start every case from.")
    run.add_argument("--video", action="store_true", help="Record a video per case.")
    run.add_argument("--trace", action="store_true", help="Record a Playwright trace per case.")
    run.add_argument("--width", type=int, default=1440)
    run.add_argument("--height", type=int, default=900)
    run.add_argument("--max-steps", type=int, default=None)
    run.add_argument("--no-vision", action="store_true", help="Do not send screenshots to the model.")
    run.add_argument(
        "--allow-page-errors", action="store_true",
        help="Do not fail a case just because the page logged console or network errors.",
    )
    run.add_argument("--junit", help="Write a JUnit XML report here.")
    run.add_argument("--json", help="Write a detailed JSON report here.")

    suites = subparsers.add_parser("suites", help="List suites.")
    suites.add_argument("--json", action="store_true")

    cases = subparsers.add_parser("cases", help="List the cases in a suite.")
    cases.add_argument("--suite", required=True)
    cases.add_argument("--json", action="store_true")

    create = subparsers.add_parser("create-suite", help="Create an empty suite.")
    create.add_argument("name")
    create.add_argument("--description")
    create.add_argument("--tag", action="append")

    add = subparsers.add_parser("add-case", help="Add a case to a suite.")
    add.add_argument("--suite", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--goal", required=True, help="What the agent should do, in plain language.")
    add.add_argument("--url")
    add.add_argument("--tag", action="append")
    add.add_argument("--auth", help="Saved sign-in profile.")
    add.add_argument("--dataset", help="CSV or JSON rows; the case runs once per row.")

    report = subparsers.add_parser("report", help="Re-emit reports for a finished suite run.")
    report.add_argument("--suite-run", required=True)
    report.add_argument("--junit")
    report.add_argument("--json")

    flaky = subparsers.add_parser("flaky", help="Which cases change their mind.")
    flaky.add_argument("--limit", type=int, default=20)
    flaky.add_argument("--json", action="store_true")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    storage.init_db()

    if args.command == "run":
        return asyncio.run(_cmd_run(args))

    handlers = {
        "suites": _cmd_suites,
        "cases": _cmd_cases,
        "create-suite": _cmd_create_suite,
        "add-case": _cmd_add_case,
        "report": _cmd_report,
        "flaky": _cmd_flaky,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
