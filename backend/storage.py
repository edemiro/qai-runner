"""SQLite persistence for test runs and their steps.

Everything the agent does is recorded, so a run survives a backend restart and
can be replayed, reported on, or exported as a script.
"""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from config import DB_PATH

# The two platforms everything is read one at a time through: a tester is
# working on web or on mobile, and mixing the two into one pass rate describes
# neither. Anything else — an empty filter, a stray query string — means "both".
PLATFORMS = ("web", "mobile")


def clean_kind(value: Optional[str]) -> Optional[str]:
    """A platform filter, or None for no filter at all."""
    kind = (value or "").strip().lower()
    return kind if kind in PLATFORMS else None


def _kind_filter(column: str, kind: Optional[str], where: List[str], params: List[Any]) -> None:
    """Add `column` = this platform to a query being assembled.

    Compared through COALESCE because `kind` was added to these tables after
    the fact: every row that predates it is NULL, and every one of them is a
    web run, because web is all there was. Matching on the bare column would
    hide that history from both tabs.
    """
    kind = clean_kind(kind)
    if kind:
        where.append(f"COALESCE({column}, 'web') = ?")
        params.append(kind)


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    goal         TEXT NOT NULL,
    status       TEXT NOT NULL,
    platform     TEXT,
    device_name  TEXT,
    device_udid  TEXT,
    app_id       TEXT,
    model        TEXT,
    started_at   REAL NOT NULL,
    finished_at  REAL,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    value       TEXT,
    reason      TEXT,
    status      TEXT NOT NULL,
    message     TEXT,
    element     TEXT,
    screenshot  TEXT,
    duration_ms INTEGER,
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, idx);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

-- A named, ordered group of cases. This is the unit CI actually runs; a single
-- run is only ever a case of one.
CREATE TABLE IF NOT EXISTS suites (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    kind        TEXT NOT NULL DEFAULT 'web',
    tags        TEXT,
    -- The module a set belongs to ("Uçuş Arama"), so several sets — one-way,
    -- round-trip, multi-city — group under one heading instead of a flat list.
    module      TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL
);

-- One scenario inside a suite. `dataset` holds rows for data-driven runs: the
-- case is executed once per row with {{placeholders}} substituted.
CREATE TABLE IF NOT EXISTS suite_cases (
    id         TEXT PRIMARY KEY,
    suite_id   TEXT NOT NULL REFERENCES suites(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    name       TEXT NOT NULL,
    goal       TEXT NOT NULL,
    url        TEXT,
    tags       TEXT,
    dataset    TEXT,
    auth_profile TEXT,
    source_run_id TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

-- One execution. Deliberately NOT tied to a suite's lifetime: an execution is
-- a record of what happened, and deleting the Test Set it was drawn from must
-- not erase the history. `name` is the label at the time it ran, so the record
-- still reads correctly once the source is gone; `suite_id` is a soft pointer
-- kept only so a still-existing set can be linked back to.
CREATE TABLE IF NOT EXISTS suite_runs (
    id          TEXT PRIMARY KEY,
    suite_id    TEXT,
    name        TEXT,
    status      TEXT NOT NULL,
    workers     INTEGER NOT NULL DEFAULT 1,
    started_at  REAL NOT NULL,
    finished_at REAL,
    error       TEXT
);

-- Which Test Sets fed an execution. A row per source, because an execution can
-- be assembled from several sets at once.
CREATE TABLE IF NOT EXISTS suite_run_sources (
    suite_run_id TEXT NOT NULL REFERENCES suite_runs(id) ON DELETE CASCADE,
    suite_id     TEXT,
    suite_name   TEXT,
    PRIMARY KEY (suite_run_id, suite_id)
);

-- Files a run produced that are too large for a column: trace.zip, video,
-- visual diffs. `path` is relative to ARTIFACT_DIR.
CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    label      TEXT,
    path       TEXT NOT NULL,
    size_bytes INTEGER,
    created_at REAL NOT NULL
);

-- Console errors and failed requests seen while a run was in flight. A run
-- that looks green but logged a 500 is not actually green.
CREATE TABLE IF NOT EXISTS page_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_idx   INTEGER,
    kind       TEXT NOT NULL,
    level      TEXT,
    text       TEXT,
    url        TEXT,
    status     INTEGER,
    created_at REAL NOT NULL
);

-- The scenario's own steps as the tester wrote them, and how each one went.
-- Distinct from `steps`, which records what the agent did: a scenario step is
-- the intent ("tap Book a flight, expect the search form"), an agent step is
-- one click. A step can take several actions, and the report has to answer
-- "did step 3 pass" without the reader counting clicks.
CREATE TABLE IF NOT EXISTS scenario_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    action      TEXT NOT NULL,
    expected    TEXT,
    status      TEXT NOT NULL,
    message     TEXT,
    actions_used INTEGER,
    duration_ms INTEGER,
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scenario_steps_run ON scenario_steps(run_id, idx);
CREATE INDEX IF NOT EXISTS idx_cases_suite ON suite_cases(suite_id, idx);
CREATE INDEX IF NOT EXISTS idx_suite_runs ON suite_runs(suite_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON page_events(run_id, step_idx);

-- A defect raised off a failed scenario. Deliberately NOT tied to the run's
-- lifetime: a bug outlives the run that found it, and deleting run history must
-- not quietly close what it raised. Everything needed to read the bug — the
-- title, the body, the frame — is copied in at the moment it is raised, so it
-- still reads correctly once the run, the case and the Test Set are gone.
CREATE TABLE IF NOT EXISTS bugs (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    detail      TEXT,
    code        TEXT,
    severity    TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    -- Soft pointers back to where it came from, for the reader who still has
    -- the history and wants the full report.
    run_id      TEXT,
    suite_run_id TEXT,
    case_id     TEXT,
    -- Copied, not joined: the names have to survive their sources.
    case_name   TEXT,
    suite_name  TEXT,
    url         TEXT,
    screenshot  TEXT,
    note        TEXT,
    -- Which platform the failure was found on. Copied from the run rather than
    -- joined, for the same reason as the names above: a bug outlives its run.
    kind        TEXT NOT NULL DEFAULT 'web',
    created_at  REAL NOT NULL,
    updated_at  REAL
);

CREATE INDEX IF NOT EXISTS idx_bugs_created ON bugs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bugs_run ON bugs(run_id);
"""

# Columns added after the first release. SQLite cannot express "add if missing"
# in DDL, so they are applied one at a time against the live table.
MIGRATIONS = [
    ("suites", "module", "TEXT"),
    # What the run actually cost the model quota. Cache reads and writes are
    # kept apart from fresh input: a cached prompt is billed at a fraction, so
    # one "input" figure would overstate a long run several times over.
    ("runs", "input_tokens", "INTEGER"),
    ("runs", "output_tokens", "INTEGER"),
    ("runs", "cache_read_tokens", "INTEGER"),
    ("runs", "cache_write_tokens", "INTEGER"),
    ("runs", "llm_calls", "INTEGER"),
    ("runs", "suite_run_id", "TEXT"),
    ("runs", "case_id", "TEXT"),
    ("runs", "tags", "TEXT"),
    ("runs", "kind", "TEXT"),
    ("runs", "verdict_note", "TEXT"),
    ("runs", "dataset_row", "TEXT"),
    ("steps", "selector", "TEXT"),
    ("steps", "healed", "INTEGER"),
    # Which scenario step this agent action was serving. A scenario step is
    # carried out by several actions, and without this the two lists could only
    # be shown side by side and left to the reader to line up — which is also
    # what stopped a green run's work from being kept and reused.
    ("steps", "scenario_idx", "INTEGER"),
    # What kind of request failed, and whose server answered. Without these a
    # report can say "1 page error" but not whether it has anything to do with
    # the feature under test.
    ("page_events", "resource_type", "TEXT"),
    ("page_events", "third_party", "INTEGER"),
    # What a failure of this scenario would actually cost, and which layer it
    # is written at — the two things the Digital Channels standard asks every
    # scenario to carry. See docs/scenario-standards.md.
    ("suite_cases", "priority", "TEXT"),
    ("suite_cases", "layer", "TEXT"),
    # Positive / Negative / Boundary. A suite of happy paths proves the feature
    # works when used correctly and nothing about what happens when it is not.
    ("suite_cases", "scenario_type", "TEXT"),
    # The state the scenario needs before its first step: signed in as whom,
    # which data already exists, what the previous search left behind. Without
    # somewhere to say it, a scenario either buries its setup in step one or
    # skips it — and then fails on the missing setup while the report names the
    # feature under test.
    ("suite_cases", "precondition", "TEXT"),
    # What the precondition needs from a person before the scenario can run:
    # a member number, a booking reference, a password. `required_data` is what
    # the scenario asks for, `precondition_data` is what the tester supplied.
    # A scenario that asks for data it has not been given cannot be run — it
    # would fail on the missing setup and the report would name the feature.
    ("suite_cases", "required_data", "TEXT"),
    ("suite_cases", "precondition_data", "TEXT"),
    # Copied onto the run when it is adopted into an execution. A report has to
    # keep reading correctly after the Test Set it came from is deleted, and a
    # join to a row that no longer exists cannot do that.
    ("runs", "case_priority", "TEXT"),
    ("runs", "case_layer", "TEXT"),
    ("runs", "case_idx", "INTEGER"),
    ("suite_runs", "name", "TEXT"),
    # Which platform the execution ran against. Derived from its Test Set at
    # the moment it starts rather than joined back later: a set can be deleted,
    # and an execution assembled from several sets has no single set to ask.
    ("suite_runs", "kind", "TEXT"),
    # Which platform the bug was found on, so Bug Report can be read one
    # platform at a time like every other page.
    ("bugs", "kind", "TEXT"),
    # Which phone OS a mobile set is written against, and which one an
    # execution drove. iOS and Android are the same product and not the same
    # screen — different controls, different labels, different selectors — so a
    # set written on one does not read on the other, and a recording taken on
    # one would be replayed into the wrong app. Null on a web set, where the
    # question does not arise.
    ("suites", "os", "TEXT"),
    ("suite_runs", "os", "TEXT"),
    # The scenario written out as ordered steps, each with what it expects to
    # see. Stored as JSON on the case: a step has no identity of its own and is
    # only ever read with the scenario it belongs to.
    ("suite_cases", "steps", "TEXT"),
]


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, and always close.

    `with sqlite3.connect(...)` only manages the transaction — it leaves the
    connection (and the file handle) open, which leaks one per call.

    The schema is re-applied if it has gone missing. Creating it only at
    startup means deleting or replacing the database file while the server is
    running turns every later write into a crash mid-request — which reaches
    the browser as an unexplained network error rather than a message.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Applying the schema on every connection would be wasteful, so it is done once
# per process — unless the `runs` table has gone missing, which means the file
# was replaced underneath us and the whole schema has to be laid down again.
_schema_ready = False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ready

    has_runs = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
    ).fetchone() is not None

    if has_runs and _schema_ready:
        return

    conn.executescript(SCHEMA)
    _apply_migrations(conn)
    _schema_ready = True


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created."""
    for table, column, coltype in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    _detach_executions_from_suites(conn)
    _backfill_execution_kind(conn)
    _backfill_execution_os(conn)
    _backfill_bug_kind(conn)


def _backfill_execution_os(conn: sqlite3.Connection) -> None:
    """Give a mobile execution the phone it ran on, where that is knowable.

    Read from the Test Sets that fed it, and only when they agree: an
    execution drawn from an iOS set and an Android one belongs to neither.
    Without this, every execution assembled by hand carried no OS — and an
    execution that does not say which phone is shown on both tabs, so an iOS
    run appeared under Android as well.
    """
    conn.execute(
        """UPDATE suite_runs SET os = (
               SELECT MIN(s.os) FROM suite_run_sources src
                 JOIN suites s ON s.id = src.suite_id
                WHERE src.suite_run_id = suite_runs.id AND s.os IS NOT NULL
                HAVING COUNT(DISTINCT s.os) = 1)
           WHERE kind = 'mobile' AND os IS NULL"""
    )


def _backfill_bug_kind(conn: sqlite3.Connection) -> None:
    """Give bugs raised before the column existed the platform of their run."""
    conn.execute(
        """UPDATE bugs SET kind = COALESCE(
               (SELECT r.kind FROM runs r WHERE r.id = bugs.run_id),
               'web')
           WHERE kind IS NULL"""
    )


def _backfill_execution_kind(conn: sqlite3.Connection) -> None:
    """Give executions that predate the `kind` column a platform.

    Read from the Test Set they came from, falling back to a run they adopted —
    an execution whose set has since been deleted still knows what it drove.
    Without this every older execution would sit outside both platform filters
    and simply not appear.
    """
    conn.execute(
        """UPDATE suite_runs SET kind = COALESCE(
               (SELECT s.kind FROM suites s WHERE s.id = suite_runs.suite_id),
               (SELECT r.kind FROM runs r
                 WHERE r.suite_run_id = suite_runs.id AND r.kind IS NOT NULL LIMIT 1),
               'web')
           WHERE kind IS NULL"""
    )


def _detach_executions_from_suites(conn: sqlite3.Connection) -> None:
    """Drop the cascade that made deleting a Test Set erase its history.

    An execution is a record of something that happened; the Test Set it was
    drawn from is a working document that gets renamed, pruned and deleted. Tying
    their lifetimes together meant tidying up the second silently destroyed the
    first. SQLite cannot drop a constraint, so the table is rebuilt.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'suite_runs'"
    ).fetchone()
    if row is None or "ON DELETE CASCADE" not in (row["sql"] or ""):
        return

    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE suite_runs_rebuilt (
            id          TEXT PRIMARY KEY,
            suite_id    TEXT,
            name        TEXT,
            status      TEXT NOT NULL,
            workers     INTEGER NOT NULL DEFAULT 1,
            started_at  REAL NOT NULL,
            finished_at REAL,
            error       TEXT
        );
        INSERT INTO suite_runs_rebuilt (id, suite_id, name, status, workers,
                                        started_at, finished_at, error)
            SELECT sr.id, sr.suite_id,
                   COALESCE((SELECT s.name FROM suites s WHERE s.id = sr.suite_id), 'Execution'),
                   sr.status, sr.workers, sr.started_at, sr.finished_at, sr.error
            FROM suite_runs sr;
        DROP TABLE suite_runs;
        ALTER TABLE suite_runs_rebuilt RENAME TO suite_runs;
        CREATE INDEX IF NOT EXISTS idx_suite_runs ON suite_runs(suite_id, started_at DESC);
        PRAGMA foreign_keys = ON;
    """)


def init_db() -> None:
    with _connect() as conn:
        _ensure_schema(conn)


def create_run(
    goal: str,
    platform: Optional[str] = None,
    device_name: Optional[str] = None,
    device_udid: Optional[str] = None,
    app_id: Optional[str] = None,
    model: Optional[str] = None,
    kind: Optional[str] = None,
    tags: Optional[List[str]] = None,
    suite_run_id: Optional[str] = None,
    case_id: Optional[str] = None,
    dataset_row: Optional[Dict[str, Any]] = None,
) -> str:
    run_id = uuid.uuid4().hex[:16]
    title = goal.strip().split("\n")[0][:80] or "Untitled run"
    with _connect() as conn:
        conn.execute(
            """INSERT INTO runs (id, title, goal, status, platform, device_name,
                                 device_udid, app_id, model, started_at,
                                 kind, tags, suite_run_id, case_id, dataset_row)
               VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, title, goal, platform, device_name, device_udid, app_id,
                model, time.time(), kind, _dump_tags(tags), suite_run_id, case_id,
                json.dumps(dataset_row, ensure_ascii=False) if dataset_row else None,
            ),
        )
    return run_id


def finish_run(
    run_id: str,
    status: str,
    error: Optional[str] = None,
    verdict_note: Optional[str] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            # COALESCE on the error too, not only the note: a run is finished
            # twice — the agent closes it with the reason it failed, then the
            # suite runner closes it again with whatever it knows, which for an
            # ordinary scenario failure is nothing. Writing that None over the
            # agent's reason is why failed runs showed an empty error.
            """UPDATE runs SET status = ?, finished_at = ?,
                               error = COALESCE(?, error),
                               verdict_note = COALESCE(?, verdict_note)
               WHERE id = ?""",
            (status, time.time(), error, verdict_note, run_id),
        )


def record_run_usage(run_id: str, usage: Optional[Dict[str, Any]]) -> None:
    """Store what this run spent at the model. Written once, when it ends."""
    if not usage:
        return
    with _connect() as conn:
        conn.execute(
            """UPDATE runs SET input_tokens = ?, output_tokens = ?,
                               cache_read_tokens = ?, cache_write_tokens = ?,
                               llm_calls = ?
               WHERE id = ?""",
            (
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0),
                usage.get("calls", 0), run_id,
            ),
        )


def usage_totals(
    days: int = 14, kind: Optional[str] = None, os: Optional[str] = None,
) -> Dict[str, Any]:
    """What the model has been asked for lately, across every run."""
    where = ["started_at >= ?", "llm_calls IS NOT NULL"]
    params: List[Any] = [time.time() - days * 86400]
    _kind_filter("kind", kind, where, params)
    _run_os_filter("platform", os, where, params, kind)
    with _connect() as conn:
        row = conn.execute(
            f"""SELECT COUNT(*) AS runs,
                      COALESCE(SUM(llm_calls), 0)          AS calls,
                      COALESCE(SUM(input_tokens), 0)       AS input_tokens,
                      COALESCE(SUM(output_tokens), 0)      AS output_tokens,
                      COALESCE(SUM(cache_read_tokens), 0)  AS cache_read_tokens,
                      COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens
                 FROM runs
                WHERE {' AND '.join(where)}""",
            params,
        ).fetchone()
    totals = dict(row) if row else {}
    totals["days"] = days
    totals["total_tokens"] = (
        totals.get("input_tokens", 0) + totals.get("output_tokens", 0)
        + totals.get("cache_read_tokens", 0) + totals.get("cache_write_tokens", 0)
    )
    return totals


def _dump_tags(tags: Optional[List[str]]) -> Optional[str]:
    """Tags are stored comma-wrapped so `tags LIKE '%,smoke,%'` matches whole
    tags only — a bare LIKE would match 'smoke' inside 'smoketest'."""
    cleaned = [t.strip().lower() for t in (tags or []) if t and t.strip()]
    return "," + ",".join(cleaned) + "," if cleaned else None


def _load_tags(raw: Optional[str]) -> List[str]:
    return [t for t in (raw or "").split(",") if t]


def add_step(
    run_id: str,
    action: str,
    status: str,
    target: Optional[str] = None,
    value: Optional[str] = None,
    reason: Optional[str] = None,
    message: Optional[str] = None,
    element: Optional[Dict[str, Any]] = None,
    screenshot: Optional[str] = None,
    duration_ms: Optional[int] = None,
    selector: Optional[str] = None,
    healed: bool = False,
    scenario_idx: Optional[int] = None,
) -> int:
    # The element the action resolved to already carries the selector that
    # reached it; taking it from there means every action records how it found
    # what it acted on, not just the ones that were handed a selector.
    if not selector and element:
        selector = element.get("xpath") or None
    with _connect() as conn:
        cursor = conn.execute("SELECT COALESCE(MAX(idx), 0) + 1 AS next FROM steps WHERE run_id = ?", (run_id,))
        idx = cursor.fetchone()["next"]
        cursor = conn.execute(
            """INSERT INTO steps (run_id, idx, action, target, value, reason, status,
                                  message, element, screenshot, duration_ms, created_at,
                                  selector, healed, scenario_idx)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, idx, action, target, value, reason, status, message,
                json.dumps(element, ensure_ascii=False) if element else None,
                screenshot, duration_ms, time.time(),
                selector, 1 if healed else 0, scenario_idx,
            ),
        )
        return cursor.lastrowid


def _row_to_run(row: sqlite3.Row) -> Dict[str, Any]:
    run = dict(row)
    started, finished = run.get("started_at"), run.get("finished_at")
    run["duration_ms"] = int((finished - started) * 1000) if started and finished else None
    run["tags"] = _load_tags(run.get("tags"))
    if run.get("dataset_row"):
        try:
            run["dataset_row"] = json.loads(run["dataset_row"])
        except Exception:
            run["dataset_row"] = None
    return run


def _row_to_step(row: sqlite3.Row, include_screenshot: bool = True) -> Dict[str, Any]:
    step = dict(row)
    if step.get("element"):
        try:
            step["element"] = json.loads(step["element"])
        except Exception:
            step["element"] = None

    # Always report whether a screenshot exists — the report needs to know that
    # without downloading every frame, and computing it only in the
    # include-everything branch meant the listing always said "none".
    step["hasScreenshot"] = bool(step.get("screenshot"))
    step["healed"] = bool(step.get("healed"))
    if not include_screenshot:
        step["screenshot"] = None
    return step


# Tables the platform tabs are drawn over. Named explicitly because the table
# goes into the SQL text: a whitelist is the difference between a helper and an
# injection point.
_PLATFORM_TABLES = ("runs", "bugs", "suites", "suite_runs")


def platform_counts(table: str) -> Dict[str, int]:
    """How much each tab would have to show, so a tab can say so before it is
    opened — an empty Mobile tab should look empty from the Web tab."""
    if table not in _PLATFORM_TABLES:
        raise ValueError(f"No platform counts for {table!r}.")
    counts = {kind: 0 for kind in PLATFORMS}
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT COALESCE(kind, 'web') AS kind, COUNT(*) AS n"
            f"  FROM {table} GROUP BY 1"
        ).fetchall()
    for row in rows:
        if row["kind"] in counts:
            counts[row["kind"]] = row["n"]
    return counts


def _run_os_filter(
    column: str, os: Optional[str], where: List[str], params: List[Any],
    kind: Optional[str] = "mobile",
) -> None:
    """Narrow to one phone, read off what the driver reported when it ran.

    An iPhone run and a Pixel run are different apps with different selectors
    and different bugs; read in one list, a failure on one looked like a
    failure on both.

    `kind` is here so the question can be refused rather than answered wrongly:
    on the Web tab there is no phone, and a stray `&os=ios` left over from the
    Mobile tab would otherwise match nothing and empty the list.
    """
    wanted = clean_os(os, kind or "mobile")
    if wanted:
        where.append(f"LOWER(COALESCE({column}, '')) = ?")
        params.append(wanted)


def _suite_os_filter(
    column: str, os: Optional[str], where: List[str], params: List[Any],
    kind: Optional[str] = "mobile",
) -> None:
    """The same cut, asked of a Test Set rather than of a run.

    A set written before the column existed has not said which phone it is
    for, and shows on both rather than being hidden by a question it predates.
    """
    wanted = clean_os(os, kind or "mobile")
    if wanted:
        where.append(f"({column} IS NULL OR {column} = ?)")
        params.append(wanted)


def _run_search_filter(search: Optional[str], where: List[str], params: List[Any]) -> None:
    """Search on the server, not in the loaded page: a filter that only looks
    at the runs already fetched quietly misses the older run being hunted for.

    Shared so the list and the counts above it agree about what matches."""
    if not (search and search.strip()):
        return
    needle = f"%{search.strip().lower()}%"
    where.append("(LOWER(r.title) LIKE ? OR LOWER(r.goal) LIKE ?"
                 " OR LOWER(COALESCE(r.app_id, '')) LIKE ?"
                 " OR LOWER(COALESCE(r.device_name, '')) LIKE ?)")
    params.extend([needle] * 4)


def run_os_counts(search: Optional[str] = None) -> Dict[str, int]:
    """How many recorded runs are on each phone, for the sub-tabs above them.

    Read from the run's own `platform`, which is what the driver reported when
    it ran — a run outlives the Test Set it came from, so asking the set is
    asking something that may no longer be there.
    """
    counts = {name: 0 for name in MOBILE_OS}
    where: List[str] = ["COALESCE(r.kind, 'web') = 'mobile'"]
    params: List[Any] = []
    _run_search_filter(search, where, params)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT LOWER(COALESCE(r.platform, '')) AS os, COUNT(*) AS n"
            f"  FROM runs r WHERE {' AND '.join(where)} GROUP BY 1",
            params,
        ).fetchall()
    for row in rows:
        if row["os"] in counts:
            counts[row["os"]] = row["n"]
    return counts


def list_runs(
    limit: int = 50,
    tag: Optional[str] = None,
    suite_run_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    search: Optional[str] = None,
    offset: int = 0,
    kind: Optional[str] = None,
    # Which phone, when the tab is Mobile. An iPhone run and a Pixel run are
    # different apps with different selectors, and reading them in one list is
    # what made a failure on one look like a failure on both.
    os: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # Priority and layer come from the run's own snapshot first: the columns
    # exist so a report survives its Test Set being deleted, and reading the
    # join alone made a run lose its band the moment the case went away.
    query = """SELECT r.*,
                      COALESCE(r.case_priority, c.priority) AS priority,
                      COALESCE(r.case_layer, c.layer)       AS layer,
                      (SELECT COUNT(*) FROM steps s WHERE s.run_id = r.id) AS step_count,
                      (SELECT COUNT(*) FROM steps s
                        WHERE s.run_id = r.id AND s.status = 'failed') AS failed_count,
                      -- page_events holds warnings as well, and third-party
                      -- failures, and counting either made this list disagree
                      -- with both the detail view and the run's own verdict.
                      (SELECT COUNT(*) FROM page_events e
                        WHERE e.run_id = r.id AND e.level = 'error'
                          AND e.third_party = 0) AS page_error_count
               FROM runs r
               LEFT JOIN suite_cases c ON c.id = r.case_id"""
    where, params = [], []
    _kind_filter("r.kind", kind, where, params)
    _run_os_filter("r.platform", os, where, params, kind)
    if priority:
        # Not `c.priority = ?`, which would also turn the LEFT JOIN into an
        # inner one and drop every run whose case has since been deleted.
        where.append("COALESCE(r.case_priority, c.priority) = ?")
        params.append(priority)
    if tag:
        where.append("r.tags LIKE ?")
        params.append(f"%,{tag.strip().lower()},%")
    if suite_run_id:
        where.append("r.suite_run_id = ?")
        params.append(suite_run_id)
    if status:
        where.append("r.status = ?")
        params.append(status)
    _run_search_filter(search, where, params)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY r.started_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_run(row) for row in rows]


def get_run(run_id: str, include_screenshots: bool = False) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        run = _row_to_run(row)
        step_rows = conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY idx ASC", (run_id,)
        ).fetchall()
    run["steps"] = [_row_to_step(r, include_screenshots) for r in step_rows]
    run["step_count"] = len(run["steps"])
    run["failed_count"] = sum(1 for s in run["steps"] if s["status"] == "failed")
    run["healed_count"] = sum(1 for s in run["steps"] if s["healed"])
    run["pageEvents"] = list_page_events(run_id)
    run["artifacts"] = list_artifacts(run_id)
    run["scenarioSteps"] = list_scenario_steps(run_id)
    return run


# --- scenario steps ------------------------------------------------------- #

def start_scenario_step(
    run_id: str, idx: int, action: str, expected: Optional[str] = None,
) -> int:
    """Open a scenario step. Written before it runs so a run that dies midway
    still shows which step it was on rather than ending at the last one that
    happened to finish."""
    with _connect() as conn:
        cursor = conn.execute(
            """INSERT INTO scenario_steps
                   (run_id, idx, action, expected, status, created_at)
               VALUES (?, ?, ?, ?, 'running', ?)""",
            (run_id, idx, action, expected or None, time.time()),
        )
        return cursor.lastrowid


def finish_scenario_step(
    step_row_id: int, status: str, message: Optional[str] = None,
    actions_used: Optional[int] = None, duration_ms: Optional[int] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE scenario_steps
                  SET status = ?, message = ?, actions_used = ?, duration_ms = ?
                WHERE id = ?""",
            (status, message, actions_used, duration_ms, step_row_id),
        )


def list_scenario_steps(run_id: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scenario_steps WHERE run_id = ? ORDER BY idx ASC", (run_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_step_screenshot(run_id: str, step_id: int) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT screenshot FROM steps WHERE run_id = ? AND id = ?", (run_id, step_id)
        ).fetchone()
    return row["screenshot"] if row else None


def delete_run(run_id: str) -> bool:
    with _connect() as conn:
        conn.execute("DELETE FROM steps WHERE run_id = ?", (run_id,))
        cursor = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        return cursor.rowcount > 0


def delete_suite_run(suite_run_id: str) -> bool:
    """Remove an execution and the runs recorded under it.

    The runs go with it: a run's report is read through the execution it
    belongs to, so leaving them behind would orphan rows nothing can reach.
    """
    with _connect() as conn:
        run_ids = [
            row["id"] for row in
            conn.execute("SELECT id FROM runs WHERE suite_run_id = ?", (suite_run_id,))
        ]
        for run_id in run_ids:
            conn.execute("DELETE FROM steps WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE suite_run_id = ?", (suite_run_id,))
        conn.execute("DELETE FROM suite_run_sources WHERE suite_run_id = ?", (suite_run_id,))
        cursor = conn.execute("DELETE FROM suite_runs WHERE id = ?", (suite_run_id,))
        return cursor.rowcount > 0


def rename_run(run_id: str, title: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute("UPDATE runs SET title = ? WHERE id = ?", (title[:120], run_id))
        return cursor.rowcount > 0


def set_run_tags(run_id: str, tags: List[str]) -> bool:
    with _connect() as conn:
        cursor = conn.execute("UPDATE runs SET tags = ? WHERE id = ?", (_dump_tags(tags), run_id))
        return cursor.rowcount > 0


def link_run_to_suite(
    run_id: str,
    suite_run_id: str,
    case_id: str,
    title: Optional[str] = None,
    tags: Optional[List[str]] = None,
    dataset_row: Optional[Dict[str, Any]] = None,
    kind: Optional[str] = None,
    priority: Optional[str] = None,
    layer: Optional[str] = None,
    case_idx: Optional[int] = None,
) -> bool:
    """Adopt a run the agent already created into a suite run.

    The agent owns run creation — it is what records the steps — so the suite
    runner must claim that run rather than opening a second one. Creating its
    own produced two records per case: the agent's, holding the steps, and the
    runner's, holding the page errors and artifacts, with neither being a
    complete report.
    """
    with _connect() as conn:
        cursor = conn.execute(
            """UPDATE runs SET suite_run_id = ?, case_id = ?,
                               title = COALESCE(?, title),
                               tags = COALESCE(?, tags),
                               dataset_row = COALESCE(?, dataset_row),
                               kind = COALESCE(?, kind),
                               case_priority = COALESCE(?, case_priority),
                               case_layer = COALESCE(?, case_layer),
                               case_idx = COALESCE(?, case_idx)
               WHERE id = ?""",
            (
                suite_run_id, case_id, title[:200] if title else None,
                _dump_tags(tags),
                json.dumps(dataset_row, ensure_ascii=False) if dataset_row else None,
                kind, priority, layer, case_idx, run_id,
            ),
        )
        return cursor.rowcount > 0


# --- bugs ----------------------------------------------------------------- #

BUG_STATUSES = ("open", "triaged", "fixed", "closed", "not-a-bug")


def create_bug(
    title: str,
    detail: Optional[str] = None,
    code: Optional[str] = None,
    severity: Optional[str] = None,
    run_id: Optional[str] = None,
    suite_run_id: Optional[str] = None,
    case_id: Optional[str] = None,
    case_name: Optional[str] = None,
    suite_name: Optional[str] = None,
    url: Optional[str] = None,
    screenshot: Optional[str] = None,
    status: str = "open",
    kind: Optional[str] = None,
) -> str:
    bug_id = uuid.uuid4().hex[:16]
    now = time.time()
    with _connect() as conn:
        # Read from the run when the caller did not say, so a bug raised from a
        # failed scenario lands on the right tab without the UI having to know.
        if clean_kind(kind) is None and run_id:
            row = conn.execute("SELECT kind FROM runs WHERE id = ?", (run_id,)).fetchone()
            kind = row["kind"] if row else None
        conn.execute(
            """INSERT INTO bugs (id, title, detail, code, severity, status, run_id,
                                 suite_run_id, case_id, case_name, suite_name, url,
                                 screenshot, kind, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bug_id, title[:300], detail, code, severity,
                status if status in BUG_STATUSES else "open",
                run_id, suite_run_id, case_id, case_name, suite_name, url,
                screenshot, clean_kind(kind) or "web", now, now,
            ),
        )
    return bug_id


def _bug_os_filter(
    os: Optional[str], where: List[str], params: List[Any],
    kind: Optional[str] = "mobile",
) -> None:
    """Narrow bugs to one phone, through the run that raised them.

    A bug filed by hand has no run and so no OS. It stays on both sub-tabs
    rather than being hidden by a question it was never asked — the same rule
    a Test Set written before the field existed follows.
    """
    wanted = clean_os(os, kind or "mobile")
    if wanted:
        where.append("(LOWER(COALESCE(r.platform, '')) = ? OR b.run_id IS NULL)")
        params.append(wanted)


def _row_to_bug(row: sqlite3.Row, include_screenshot: bool = False) -> Dict[str, Any]:
    bug = dict(row)
    bug["hasScreenshot"] = bool(bug.get("screenshot"))
    if not include_screenshot:
        bug.pop("screenshot", None)
    return bug


def list_bugs(
    status: Optional[str] = None,
    code: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
    kind: Optional[str] = None,
    os: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # Which phone a bug is on is read off the run that raised it rather than
    # stored again: bugs carry the run, and the run carries what the driver
    # reported. One filed by hand has no run and so no OS, which puts it on
    # both sub-tabs — the same rule everything else here follows.
    query = ("SELECT b.*,"
             "       CASE WHEN LOWER(COALESCE(r.platform, '')) IN ('ios', 'android')"
             "            THEN LOWER(r.platform) END AS os"
             "  FROM bugs b LEFT JOIN runs r ON r.id = b.run_id")
    where, params = [], []
    _kind_filter("b.kind", kind, where, params)
    _bug_os_filter(os, where, params, kind)
    if status:
        where.append("b.status = ?")
        params.append(status)
    if code:
        where.append("b.code = ?")
        params.append(code)
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        where.append("(LOWER(b.title) LIKE ? OR LOWER(COALESCE(b.detail, '')) LIKE ?"
                     " OR LOWER(COALESCE(b.case_name, '')) LIKE ?)")
        params.extend([needle, needle, needle])
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY b.created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_bug(row) for row in rows]


def get_bug(bug_id: str, include_screenshot: bool = False) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM bugs WHERE id = ?", (bug_id,)).fetchone()
    return _row_to_bug(row, include_screenshot) if row else None


def update_bug(bug_id: str, **fields: Any) -> bool:
    allowed = {"title", "detail", "code", "severity", "status", "note"}
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed and value is not None:
            if key == "status" and value not in BUG_STATUSES:
                continue
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return False
    sets.append("updated_at = ?")
    values.extend([time.time(), bug_id])
    with _connect() as conn:
        cursor = conn.execute(f"UPDATE bugs SET {', '.join(sets)} WHERE id = ?", values)
    return cursor.rowcount > 0


def delete_bug(bug_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM bugs WHERE id = ?", (bug_id,))
    return cursor.rowcount > 0


def bug_for_run(run_id: str) -> Optional[Dict[str, Any]]:
    """The bug already raised for this run, if there is one.

    Raising a second one for the same failed scenario buys nothing and makes
    the list unreadable, so the UI offers to open the existing one instead.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bugs WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    return _row_to_bug(row) if row else None


def bug_counts(kind: Optional[str] = None, os: Optional[str] = None) -> Dict[str, int]:
    # Counted within the platform being viewed: the status filter sits under
    # the platform tab, so "12 open" has to mean 12 on this tab — and on the
    # phone it names, once Mobile is split in two.
    where: List[str] = []
    params: List[Any] = []
    _kind_filter("b.kind", kind, where, params)
    _bug_os_filter(os, where, params, kind)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT b.status AS status, COUNT(*) AS n"
            "  FROM bugs b LEFT JOIN runs r ON r.id = b.run_id"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " GROUP BY 1",
            params,
        ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    counts["all"] = sum(counts.values())
    return counts


def bug_os_counts(kind: str = "mobile") -> Dict[str, int]:
    """How many bugs sit on each phone, for the sub-tabs above them."""
    counts = {name: 0 for name in MOBILE_OS}
    where: List[str] = []
    params: List[Any] = []
    _kind_filter("b.kind", kind, where, params)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT LOWER(COALESCE(r.platform, '')) AS os, COUNT(*) AS n"
            "  FROM bugs b LEFT JOIN runs r ON r.id = b.run_id"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " GROUP BY 1",
            params,
        ).fetchall()
    for row in rows:
        if row["os"] in counts:
            counts[row["os"]] = row["n"]
    return counts


# --- page events (console / network) ------------------------------------- #

def add_page_events(run_id: str, events: List[Dict[str, Any]], step_idx: Optional[int] = None) -> int:
    """Record what the page complained about. Returns how many were stored."""
    if not events:
        return 0
    now = time.time()
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO page_events (run_id, step_idx, kind, level, text, url, status,
                                        created_at, resource_type, third_party)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id,
                    event.get("stepIdx", step_idx),
                    event.get("kind", "console"),
                    event.get("level"),
                    (event.get("text") or "")[:2000],
                    event.get("url"),
                    event.get("status"),
                    event.get("at", now),
                    event.get("resourceType"),
                    1 if event.get("thirdParty") else 0,
                )
                for event in events
            ],
        )
    return len(events)


def list_page_events(run_id: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM page_events WHERE run_id = ? ORDER BY id ASC", (run_id,)
        ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        # The frontend reads camelCase; the column names stay snake_case.
        event["resourceType"] = event.get("resource_type")
        event["thirdParty"] = bool(event.get("third_party"))
        events.append(event)
    return events


# --- artifacts ------------------------------------------------------------ #

def add_artifact(
    run_id: str, kind: str, path: str, label: Optional[str] = None,
    size_bytes: Optional[int] = None,
) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """INSERT INTO artifacts (run_id, kind, label, path, size_bytes, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, kind, label, path, size_bytes, time.time()),
        )
        return cursor.lastrowid


def list_artifacts(run_id: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY id ASC", (run_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_artifact(artifact_id: int) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    return dict(row) if row else None


# --- suites --------------------------------------------------------------- #

MOBILE_OS = ("ios", "android")


def clean_os(value: Optional[str], kind: str = "mobile") -> Optional[str]:
    """Which phone OS this is for, or None where the question does not arise.

    Only a mobile set has one. A web set carrying an OS would be a set that
    claims to be two things at once, and the lists are read through exactly
    these two fields.
    """
    if (kind or "").lower() != "mobile":
        return None
    os_name = (value or "").strip().lower()
    return os_name if os_name in MOBILE_OS else None


def create_suite(
    name: str, description: Optional[str] = None, kind: str = "web",
    tags: Optional[List[str]] = None, module: Optional[str] = None,
    os: Optional[str] = None,
) -> str:
    suite_id = uuid.uuid4().hex[:16]
    module = " ".join((module or "").split())[:80] or None
    with _connect() as conn:
        conn.execute(
            """INSERT INTO suites (id, name, description, kind, tags, module, os, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (suite_id, name[:120], description, kind, _dump_tags(tags), module,
             clean_os(os, kind), time.time()),
        )
    return suite_id


def update_suite(suite_id: str, **fields: Any) -> bool:
    allowed = {"name", "description", "kind", "module", "os"}
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed and value is not None:
            sets.append(f"{key} = ?")
            values.append(value)
    # None means "leave the tags alone", which is what a PATCH that does not
    # mention them is saying. Writing [] there emptied the tags of every set
    # anyone renamed.
    if fields.get("tags") is not None:
        sets.append("tags = ?")
        values.append(_dump_tags(fields["tags"]))
    if not sets:
        return False
    sets.append("updated_at = ?")
    values.extend([time.time(), suite_id])
    with _connect() as conn:
        cursor = conn.execute(f"UPDATE suites SET {', '.join(sets)} WHERE id = ?", values)
        return cursor.rowcount > 0


def delete_suite(suite_id: str) -> bool:
    """Remove a Test Set and its scenarios.

    The executions stay. An execution is a record of something that happened
    on a given day, and tidying up the working document it was drawn from must
    not destroy the history — see the test of the same name.
    """
    with _connect() as conn:
        conn.execute("DELETE FROM suite_cases WHERE suite_id = ?", (suite_id,))
        cursor = conn.execute("DELETE FROM suites WHERE id = ?", (suite_id,))
        return cursor.rowcount > 0


def _row_to_suite(row: sqlite3.Row) -> Dict[str, Any]:
    suite = dict(row)
    suite["tags"] = _load_tags(suite.get("tags"))
    return suite


def list_suites() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM suite_cases c
                        WHERE c.suite_id = s.id AND c.enabled = 1) AS case_count
               FROM suites s ORDER BY s.created_at DESC"""
        ).fetchall()
    return [_row_to_suite(row) for row in rows]


def get_suite(suite_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM suites WHERE id = ?", (suite_id,)).fetchone()
        if row is None:
            return None
        suite = _row_to_suite(row)
        case_rows = conn.execute(
            "SELECT * FROM suite_cases WHERE suite_id = ? ORDER BY idx ASC", (suite_id,)
        ).fetchall()
    suite["cases"] = [_row_to_case(r) for r in case_rows]
    suite["case_count"] = sum(1 for c in suite["cases"] if c["runnable"])
    # Shown beside it: a set whose count dropped needs to say why.
    suite["awaiting_data"] = sum(1 for c in suite["cases"] if c["needsData"])
    return suite


# --- suite cases ---------------------------------------------------------- #

MAX_STEPS = 40

# How many recorded actions a step may carry back. A step that took more than
# this to carry out is one the agent struggled with, and a recording of a
# struggle is not worth replaying — the budget that cuts a step off is 12.
MAX_RECORDED_ACTIONS = 12

# What a recorded action keeps. Deliberately the minimum needed to perform it
# again: everything else — the screenshot, the timing, the model's reasoning —
# belongs to the run that produced it, not to the scenario.
_RECORDED_FIELDS = ("action", "selector", "value", "label")


def clean_recorded(raw: Any) -> List[Dict[str, Any]]:
    """The actions a step was last carried out with, in a shape it can be
    carried out with again.

    An action with nothing to act on is dropped: it could not be replayed, and
    a recording with a hole in it is worse than no recording, because the
    replay would skip a step and then assert against a screen that never
    reached the state the assertion describes.
    """
    if not isinstance(raw, list):
        return []
    actions = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = " ".join(str(entry.get("action") or "").split()).lower()
        if not kind:
            continue
        if kind in NOT_REPLAYABLE:
            return []
        selector = (str(entry.get("selector") or "").strip())[:400] or None
        value = entry.get("value")
        value = (str(value).strip())[:600] if value is not None else None
        # Only the actions that reach into the page need one; `wait`, `key` and
        # `scroll` act on the page as a whole.
        if not selector and kind not in ACTIONS_WITHOUT_A_TARGET:
            return []
        actions.append({
            "action": kind, "selector": selector, "value": value,
            "label": (str(entry.get("label") or "").strip())[:120] or None,
        })
        if len(actions) > MAX_RECORDED_ACTIONS:
            return []
    return actions


# Acting on the page rather than on something in it, so a recording of one is
# complete without a selector.
#
# The assertions here are the reason this list matters rather than being a
# detail: `assert_text` and `assert_absent` read the screen's text and are
# driven entirely by the value they look for, so they never carry a selector —
# and while they were treated as incomplete, a single one of them at the end of
# a step threw away the recording for every action in it. Measured on five
# scenarios, that was six of the twenty steps. `assert_visible` is not here: it
# resolves an element, so it does need one.
ACTIONS_WITHOUT_A_TARGET = {
    "wait", "key", "scroll", "swipe", "navigate", "back",
    "assert_text", "assert_absent", "assert_no_errors", "assert_visual",
}

# Actions a recording cannot carry, so a step containing one keeps none.
# `assert_disabled` resolves against a live snapshot's elementId, which a
# recording does not have; `explore` and the authoring verbs are about reading
# the screen rather than driving it; the rest close the step or the run and are
# the loop's business, not the page's.
NOT_REPLAYABLE = {
    "assert_disabled", "explore", "write_scenarios", "run_test_set",
    "step_done", "done", "finish", "complete",
}


def drop_stale_recordings(
    existing: Optional[Dict[str, Any]], steps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Take the recording off any step whose instruction has changed.

    The runner replays a recording instead of working the step out again, so a
    step that now says something different has to be worked out again. The
    danger is not the wasted effort — it is a recorded assertion general enough
    to still hold, which would report the rewritten step as verified without it
    ever having been carried out.

    Matched on what the step says rather than on where it sits, so reordering a
    scenario costs nothing: a step that moved is the same step, and only a step
    whose wording no longer appears in the saved scenario loses its recording.
    """
    said_before = {
        (step.get("action"), step.get("expected"))
        for step in ((existing or {}).get("steps") or [])
    }
    for step in steps:
        if "recorded" in step and (step.get("action"), step.get("expected")) not in said_before:
            step.pop("recorded")
    return steps


def clean_steps(raw: Any) -> List[Dict[str, Any]]:
    """Ordered steps, each an instruction and what it should produce.

    Anything without an instruction is dropped rather than stored: a step with
    no action is a row the runner would have to skip and the report would have
    to explain.

    Shared by everything that accepts steps — the API, the step editor and the
    scenario generator — so a step means the same thing however it arrived.
    The count is capped because the runner budgets its actions per step: a
    scenario with hundreds of steps is a malformed one, and running it would
    tie up a device for hours before anyone saw the mistake.
    """
    if not isinstance(raw, list):
        return []
    steps = []
    for entry in raw:
        if isinstance(entry, str):
            entry = {"action": entry}
        if not isinstance(entry, dict):
            continue
        action = " ".join(str(entry.get("action") or "").split())
        if not action:
            continue
        expected = " ".join(str(entry.get("expected") or "").split())
        step: Dict[str, Any] = {"action": action[:600], "expected": expected[:600]}
        # Carried through every edit of the scenario. A step whose wording
        # changes keeps its recording; if the wording changed enough to mean
        # something else, the replay fails its assertion and the step is handed
        # back to the model, which re-records it.
        recorded = clean_recorded(entry.get("recorded"))
        if recorded:
            step["recorded"] = recorded
        steps.append(step)
    return steps[:MAX_STEPS]


def _row_to_case(row: sqlite3.Row) -> Dict[str, Any]:
    case = dict(row)
    case["tags"] = _load_tags(case.get("tags"))
    case["enabled"] = bool(case.get("enabled"))
    if case.get("dataset"):
        try:
            case["dataset"] = json.loads(case["dataset"])
        except Exception:
            case["dataset"] = None
    try:
        case["steps"] = json.loads(case["steps"]) if case.get("steps") else []
    except Exception:
        case["steps"] = []
    for field, empty in (("required_data", []), ("precondition_data", {})):
        try:
            case[field] = json.loads(case[field]) if case.get(field) else empty
        except Exception:
            case[field] = empty
    # How much of this scenario the next run already knows how to do. A step
    # with a recording is replayed rather than reasoned about, so this is the
    # difference between a run that costs a model call per action and one that
    # costs none — worth saying on the scenario rather than leaving a tester to
    # infer it from a bill.
    case["recordedSteps"] = sum(1 for step in case["steps"] if step.get("recorded"))
    case["stepCount"] = len(case["steps"])
    # Computed rather than stored: the answer changes the moment someone fills
    # a field in, and two copies of it would disagree.
    case["missingData"] = missing_data(case)
    case["needsData"] = bool(case["missingData"])
    # What an execution actually asks. A scenario waiting on its setup is not
    # runnable however its enabled flag reads.
    case["runnable"] = case["enabled"] and not case["needsData"]
    return case


def clean_required_data(value: Any) -> List[Dict[str, str]]:
    """The fields a scenario asks for, in the one shape the app uses.

    A request with no key is unusable — there would be nothing to store the
    answer under — so it is dropped rather than half-kept.
    """
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key = " ".join(str(item.get("key") or "").split())
        if not key:
            continue
        cleaned.append({
            "key": key[:60],
            "label": (str(item.get("label") or key).strip())[:120],
            "example": (str(item.get("example") or "").strip())[:120],
        })
    return cleaned


def missing_data(case: Dict[str, Any]) -> List[str]:
    """Which of the fields this scenario asked for have not been answered."""
    required = case.get("required_data") or []
    supplied = case.get("precondition_data") or {}
    if not isinstance(supplied, dict):
        supplied = {}
    return [
        field["key"] for field in required
        if not str(supplied.get(field["key"]) or "").strip()
    ]


def add_case(
    suite_id: str, name: str, goal: str, url: Optional[str] = None,
    tags: Optional[List[str]] = None, dataset: Optional[List[Dict[str, Any]]] = None,
    auth_profile: Optional[str] = None, source_run_id: Optional[str] = None,
    priority: Optional[str] = None, layer: Optional[str] = None,
    steps: Optional[List[Dict[str, str]]] = None,
    scenario_type: Optional[str] = None,
    precondition: Optional[str] = None,
    required_data: Optional[List[Dict[str, str]]] = None,
    precondition_data: Optional[Dict[str, str]] = None,
    enabled: Optional[bool] = None,
) -> str:
    case_id = uuid.uuid4().hex[:16]
    cleaned_steps = clean_steps(steps)
    cleaned_required = clean_required_data(required_data)
    supplied = precondition_data if isinstance(precondition_data, dict) else {}
    # A scenario that still needs data starts disabled. Running it would fail
    # on the missing setup, and the report would blame the feature — so it sits
    # out of every execution until someone answers what it asked for.
    if enabled is None:
        enabled = not missing_data({
            "required_data": cleaned_required, "precondition_data": supplied,
        })
    with _connect() as conn:
        idx = conn.execute(
            "SELECT COALESCE(MAX(idx), 0) + 1 AS next FROM suite_cases WHERE suite_id = ?",
            (suite_id,),
        ).fetchone()["next"]
        conn.execute(
            """INSERT INTO suite_cases (id, suite_id, idx, name, goal, url, tags,
                                        dataset, auth_profile, source_run_id,
                                        priority, layer, steps, scenario_type,
                                        precondition, required_data,
                                        precondition_data, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                case_id, suite_id, idx, name[:200], goal, url, _dump_tags(tags),
                json.dumps(dataset, ensure_ascii=False) if dataset else None,
                auth_profile, source_run_id, priority, layer,
                json.dumps(cleaned_steps, ensure_ascii=False) if cleaned_steps else None,
                scenario_type, precondition,
                json.dumps(cleaned_required, ensure_ascii=False) if cleaned_required else None,
                json.dumps(supplied, ensure_ascii=False) if supplied else None,
                1 if enabled else 0,
                time.time(),
            ),
        )
    return case_id


def update_case(case_id: str, **fields: Any) -> bool:
    # priority and layer are editable: the generator proposes them from the
    # standard, but the tester who knows the business flow has the last word.
    allowed = {
        "name", "goal", "url", "auth_profile", "idx",
        "priority", "layer", "scenario_type", "precondition",
    }
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed and value is not None:
            sets.append(f"{key} = ?")
            values.append(value)
    if "tags" in fields:
        sets.append("tags = ?")
        values.append(_dump_tags(fields["tags"]))
    if "steps" in fields:
        cleaned = drop_stale_recordings(get_case(case_id), clean_steps(fields["steps"]))
        sets.append("steps = ?")
        values.append(json.dumps(cleaned, ensure_ascii=False) if cleaned else None)
    if "required_data" in fields:
        required = clean_required_data(fields["required_data"])
        sets.append("required_data = ?")
        values.append(json.dumps(required, ensure_ascii=False) if required else None)
    if "precondition_data" in fields:
        supplied = fields["precondition_data"]
        supplied = supplied if isinstance(supplied, dict) else {}
        sets.append("precondition_data = ?")
        values.append(json.dumps(supplied, ensure_ascii=False) if supplied else None)
    if "dataset" in fields:
        sets.append("dataset = ?")
        values.append(
            json.dumps(fields["dataset"], ensure_ascii=False) if fields["dataset"] else None
        )
    if "enabled" in fields:
        sets.append("enabled = ?")
        values.append(1 if fields["enabled"] else 0)
    # Answering what the scenario asked for turns it back on. It was disabled
    # because it was waiting on this and nothing else, so leaving the tester to
    # flip a second switch afterwards would be asking them to say yes twice.
    # An explicit `enabled` in the same update still wins: someone turning a
    # scenario off on purpose is not overruled by filling in its data.
    if "precondition_data" in fields and "enabled" not in fields:
        current = get_case(case_id)
        if current is not None:
            merged = dict(current)
            merged["precondition_data"] = (
                fields["precondition_data"]
                if isinstance(fields["precondition_data"], dict) else {}
            )
            if not missing_data(merged):
                sets.append("enabled = ?")
                values.append(1)

    if not sets:
        return False
    values.append(case_id)
    with _connect() as conn:
        cursor = conn.execute(f"UPDATE suite_cases SET {', '.join(sets)} WHERE id = ?", values)
        return cursor.rowcount > 0


def move_cases(case_ids: List[str], suite_id: str) -> int:
    """Re-file scenarios into another Test Set, appended in the order given.

    This is how a set that grew from several different requests gets split
    back into one set per request. Runs and executions keep pointing at the
    same case ids, so history follows the scenario to its new set.
    """
    moved = 0
    with _connect() as conn:
        for case_id in case_ids:
            cursor = conn.execute(
                """UPDATE suite_cases
                      SET suite_id = ?,
                          idx = (SELECT COALESCE(MAX(idx), 0) + 1
                                   FROM suite_cases WHERE suite_id = ?)
                    WHERE id = ? AND suite_id != ?""",
                (suite_id, suite_id, case_id, suite_id),
            )
            moved += cursor.rowcount
        conn.execute("UPDATE suites SET updated_at = ? WHERE id = ?", (time.time(), suite_id))
    return moved


def delete_case(case_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM suite_cases WHERE id = ?", (case_id,))
        return cursor.rowcount > 0


def promote_recording(run_id: str, case_id: str) -> int:
    """Keep what a green run did, on the scenario that ran.

    Every execution re-derived the same clicks from the same screens: a
    screenshot and a model call per action, paid again on every run of a
    scenario that had not changed. The run already knows which element each
    action reached and how it addressed it, so the second run of a scenario has
    no reason to ask the model how to do what the first one already did.

    Only a green run is kept. A recording taken from a failed one would make
    the next run repeat the same wrong move, faster and without the model
    present to notice — and a scenario that has never passed has nothing worth
    learning from. Returns how many steps came away with a recording.
    """
    case = get_case(case_id)
    if case is None:
        return 0
    steps = case.get("steps") or []
    if not steps:
        return 0

    with _connect() as conn:
        run = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None or run["status"] != "passed":
            return 0
        rows = conn.execute(
            """SELECT scenario_idx, action, selector, value, target, status
                 FROM steps
                WHERE run_id = ? AND scenario_idx IS NOT NULL
                ORDER BY idx ASC""",
            (run_id,),
        ).fetchall()
        # A scenario step is only worth keeping if every action under it
        # worked. One failure in the middle means the agent recovered by doing
        # something else, and replaying the recovery without what prompted it
        # reproduces the mistake, not the fix.
        by_step: Dict[int, List[Dict[str, Any]]] = {}
        spoiled = set()
        for row in rows:
            idx = row["scenario_idx"]
            if row["status"] != "passed":
                spoiled.add(idx)
                continue
            by_step.setdefault(idx, []).append({
                "action": row["action"], "selector": row["selector"],
                "value": row["value"], "label": row["target"],
            })

        kept = 0
        for position, step in enumerate(steps, start=1):
            recorded = clean_recorded(by_step.get(position) or [])
            if position in spoiled or not recorded:
                step.pop("recorded", None)
                continue
            step["recorded"] = recorded
            kept += 1

        conn.execute(
            "UPDATE suite_cases SET steps = ? WHERE id = ?",
            (json.dumps(steps, ensure_ascii=False), case_id),
        )
    return kept


def get_case(case_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM suite_cases WHERE id = ?", (case_id,)).fetchone()
    return _row_to_case(row) if row else None


def select_cases(suite_id: str, tags: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The runnable cases of a suite, optionally narrowed to those carrying any
    of `tags` — this is what `--tag smoke` on the CLI resolves to.

    Runnable, not merely enabled: a scenario still waiting on the data its
    precondition asked for would fail on the missing setup, and the report
    would name the feature under test rather than the absent member number.
    """
    suite = get_suite(suite_id)
    if suite is None:
        return []
    cases = [case for case in suite["cases"] if case["runnable"]]
    if tags:
        wanted = {t.strip().lower() for t in tags if t and t.strip()}
        cases = [case for case in cases if wanted & set(case["tags"])]
    return cases


def cases_by_id(case_ids: List[str]) -> List[Dict[str, Any]]:
    """The named cases, in the order given, from wherever they live.

    An execution is assembled by picking scenarios — sometimes a handful out of
    one Test Set, sometimes across several — so selection cannot be expressed as
    "a suite, filtered". The caller's order is preserved because it is the order
    they chose.
    """
    wanted = [cid for cid in dict.fromkeys(case_ids or []) if cid]
    if not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    with _connect() as conn:
        rows = conn.execute(
            # The set's platform travels with the case: an execution assembled
            # by hand has no set of its own to ask, and without the OS it lands
            # on neither phone tab — or, because an execution that does not say
            # shows on both, on both at once.
            f"""SELECT c.*, s.name AS suite_name, s.kind AS suite_kind, s.os AS suite_os
                FROM suite_cases c LEFT JOIN suites s ON s.id = c.suite_id
                WHERE c.id IN ({marks})""",
            wanted,
        ).fetchall()
    found = {row["id"]: _row_to_case(row) for row in rows}
    return [found[cid] for cid in wanted if cid in found]


# --- suite runs ----------------------------------------------------------- #

def create_suite_run(
    suite_id: Optional[str] = None,
    workers: int = 1,
    name: Optional[str] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    kind: Optional[str] = None,
    os: Optional[str] = None,
) -> str:
    """Open an execution.

    `sources` records every Test Set that fed it, because an execution can be
    assembled from more than one. `name` is stored rather than looked up so the
    record still reads correctly after those sets are gone.
    """
    suite_run_id = uuid.uuid4().hex[:16]
    contributing = sources or ([{"suite_id": suite_id}] if suite_id else [])

    with _connect() as conn:
        labels = []
        for source in contributing:
            label = source.get("suite_name")
            if not label and source.get("suite_id"):
                row = conn.execute(
                    "SELECT name FROM suites WHERE id = ?", (source["suite_id"],)
                ).fetchone()
                label = row["name"] if row else None
            labels.append(label or "Test Set")
            source["suite_name"] = label

        conn.execute(
            """INSERT INTO suite_runs (id, suite_id, name, kind, os, status, workers, started_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
            (
                suite_run_id,
                suite_id or (contributing[0].get("suite_id") if contributing else None),
                name or " + ".join(dict.fromkeys(labels)) or "Execution",
                kind or "web",
                clean_os(os, kind or "web"),
                workers,
                time.time(),
            ),
        )
        for source in contributing:
            conn.execute(
                """INSERT OR REPLACE INTO suite_run_sources
                       (suite_run_id, suite_id, suite_name) VALUES (?, ?, ?)""",
                (suite_run_id, source.get("suite_id"), source.get("suite_name")),
            )
    return suite_run_id


def finish_suite_run(suite_run_id: str, status: str, error: Optional[str] = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE suite_runs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
            (status, time.time(), error, suite_run_id),
        )


def get_suite_run(suite_run_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM suite_runs WHERE id = ?", (suite_run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        # The scenario's number and priority come from the case, not the run:
        # an execution is read as "which scenarios passed", and a failed
        # Critical is a different morning to a failed Low.
        # Read from the run's own snapshot, falling back to the case only while
        # it still exists: a report has to survive its Test Set being deleted.
        run_rows = conn.execute(
            # case_idx is listed before r.* on purpose: r.* carries a column of
            # that name too, and sqlite3.Row keeps the *first* of a duplicate
            # pair, so putting the COALESCE second silently discarded it.
            """SELECT COALESCE(r.case_idx, c.idx)           AS case_idx,
                      r.*,
                      COALESCE(r.case_priority, c.priority) AS priority,
                      COALESCE(r.case_layer, c.layer)       AS layer,
                      (SELECT COUNT(*) FROM steps s WHERE s.run_id = r.id) AS step_count,
                      (SELECT COUNT(*) FROM steps s
                        WHERE s.run_id = r.id AND s.status = 'failed') AS failed_count
               FROM runs r
               LEFT JOIN suite_cases c ON c.id = r.case_id
               WHERE r.suite_run_id = ?
               ORDER BY COALESCE(r.case_idx, c.idx) ASC, r.started_at ASC""",
            (suite_run_id,),
        ).fetchall()
        suite = conn.execute(
            "SELECT name FROM suites WHERE id = ?", (result["suite_id"],)
        ).fetchone()
        sources = conn.execute(
            """SELECT suite_id, suite_name FROM suite_run_sources
               WHERE suite_run_id = ?""",
            (suite_run_id,),
        ).fetchall()

    # The stored name wins: it is what the execution was called when it ran, and
    # the set it came from may since have been renamed or deleted.
    result["suite_name"] = result.get("name") or (suite["name"] if suite else None)
    result["sources"] = [dict(row) for row in sources]
    result["suite_exists"] = suite is not None
    result["runs"] = [_row_to_run(r) for r in run_rows]
    result["passed"] = sum(1 for r in result["runs"] if r["status"] == "passed")
    result["failed"] = sum(1 for r in result["runs"] if r["status"] == "failed")
    started, finished = result.get("started_at"), result.get("finished_at")
    result["duration_ms"] = int((finished - started) * 1000) if started and finished else None
    return result


def list_suite_runs(suite_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    query = """SELECT sr.*,
                      COALESCE(sr.name, s.name, 'Execution') AS suite_name,
                      (SELECT COUNT(*) FROM suite_run_sources q
                        WHERE q.suite_run_id = sr.id) AS source_count,
                      (SELECT COUNT(*) FROM runs r WHERE r.suite_run_id = sr.id) AS total,
                      (SELECT COUNT(*) FROM runs r
                        WHERE r.suite_run_id = sr.id AND r.status = 'passed') AS passed
               FROM suite_runs sr LEFT JOIN suites s ON s.id = sr.suite_id"""
    params: List[Any] = []
    if suite_id:
        query += " WHERE sr.suite_id = ?"
        params.append(suite_id)
    query += " ORDER BY sr.started_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


# --- history / flakiness -------------------------------------------------- #

def case_history(case_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, status, started_at, finished_at FROM runs
               WHERE case_id = ? ORDER BY started_at DESC LIMIT ?""",
            (case_id, limit),
        ).fetchall()
    return [_row_to_run(row) for row in rows]


def flakiness_report(
    limit: int = 40, window: int = 20, kind: Optional[str] = None,
    os: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Which cases change their mind.

    A case that always fails is broken and easy to see; a case that passes most
    of the time and fails occasionally is the one that erodes trust in the
    suite, so both the rate and the alternation count are reported.
    """
    # Filtered on the Test Set rather than on the runs: a case belongs to one
    # platform for its whole life, and reading it from the set keeps a case
    # whose runs all predate the column on the tab it actually belongs to.
    where: List[str] = []
    params: List[Any] = []
    _kind_filter("s.kind", kind, where, params)
    _suite_os_filter("s.os", os, where, params, kind)
    with _connect() as conn:
        case_rows = conn.execute(
            """SELECT c.id, c.name, c.suite_id, s.name AS suite_name
               FROM suite_cases c LEFT JOIN suites s ON s.id = c.suite_id"""
            + (" WHERE " + " AND ".join(where) if where else ""),
            params,
        ).fetchall()

        report = []
        for case in case_rows:
            runs = conn.execute(
                """SELECT status FROM runs WHERE case_id = ? AND status IN ('passed', 'failed')
                   ORDER BY started_at DESC LIMIT ?""",
                (case["id"], window),
            ).fetchall()
            statuses = [r["status"] for r in runs]
            if len(statuses) < 2:
                continue
            failures = sum(1 for s in statuses if s == "failed")
            flips = sum(1 for a, b in zip(statuses, statuses[1:]) if a != b)
            report.append({
                "case_id": case["id"],
                "name": case["name"],
                "suite_id": case["suite_id"],
                "suite_name": case["suite_name"],
                "runs": len(statuses),
                "failures": failures,
                "pass_rate": round((len(statuses) - failures) / len(statuses), 3),
                "flips": flips,
                # Alternating results score higher than a uniformly failing case.
                "flakiness": round(flips / max(len(statuses) - 1, 1), 3),
                "last_status": statuses[0],
            })

    report.sort(key=lambda item: (-item["flakiness"], -item["failures"]))
    return report[:limit]


PRIORITY_ORDER = ("Critical", "High", "Medium", "Low")


def priority_breakdown(
    days: int = 14, kind: Optional[str] = None, os: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Pass/fail per priority band.

    A pass rate on its own does not say whether the team is in trouble: the same
    number is routine when the failures are Low and an emergency when they are
    Critical. That is the whole reason the priority standard exists, so the
    history is read through it too.

    Only runs that came from a Test Set scenario carry a priority; ad-hoc runs
    from the chat are counted separately rather than silently bucketed as
    Medium, which would misreport both groups.
    """
    where = ["r.started_at >= ?"]
    params: List[Any] = [time.time() - days * 86400]
    _kind_filter("r.kind", kind, where, params)
    _run_os_filter("r.platform", os, where, params, kind)
    with _connect() as conn:
        rows = conn.execute(
            # Grouped on the run's own snapshot first, falling back to the case
            # only while it still exists: reading the join alone dropped every
            # run whose Test Set had since been deleted into "unclassified",
            # losing the band it actually ran at.
            f"""SELECT COALESCE(r.case_priority, c.priority) AS priority,
                      SUM(r.status = 'passed') AS passed,
                      SUM(r.status = 'failed') AS failed,
                      COUNT(*) AS total
               FROM runs r
               LEFT JOIN suite_cases c ON c.id = r.case_id
               WHERE {' AND '.join(where)}
               GROUP BY COALESCE(r.case_priority, c.priority)""",
            params,
        ).fetchall()

    counts = {row["priority"]: row for row in rows}
    breakdown = []
    for band in PRIORITY_ORDER:
        row = counts.get(band)
        passed = (row["passed"] or 0) if row else 0
        failed = (row["failed"] or 0) if row else 0
        total = (row["total"] or 0) if row else 0
        breakdown.append({
            "priority": band,
            "passed": passed,
            "failed": failed,
            "total": total,
            "pass_rate": round(passed / total * 100) if total else None,
        })

    unclassified = counts.get(None)
    if unclassified and (unclassified["total"] or 0):
        breakdown.append({
            "priority": None,
            "passed": unclassified["passed"] or 0,
            "failed": unclassified["failed"] or 0,
            "total": unclassified["total"] or 0,
            "pass_rate": round((unclassified["passed"] or 0) / unclassified["total"] * 100),
        })
    return breakdown


def trend(
    days: int = 14, kind: Optional[str] = None, os: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Pass/fail counts per day, for the report's sparkline."""
    where = ["started_at >= ?"]
    params: List[Any] = [time.time() - days * 86400]
    _kind_filter("kind", kind, where, params)
    _run_os_filter("platform", os, where, params, kind)
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT date(started_at, 'unixepoch', 'localtime') AS day,
                      SUM(status = 'passed') AS passed,
                      SUM(status = 'failed') AS failed,
                      COUNT(*) AS total,
                      AVG(CASE WHEN finished_at IS NOT NULL
                               THEN (finished_at - started_at) * 1000 END) AS avg_ms
               FROM runs WHERE {' AND '.join(where)}
               GROUP BY day ORDER BY day ASC""",
            params,
        ).fetchall()
    return [
        {
            "day": row["day"],
            "passed": row["passed"] or 0,
            "failed": row["failed"] or 0,
            "total": row["total"] or 0,
            "avg_ms": int(row["avg_ms"]) if row["avg_ms"] else None,
        }
        for row in rows
    ]
