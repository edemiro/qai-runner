"""Reading the history one platform at a time.

Web and mobile share a database and almost nothing else: different drivers,
different devices, different people fixing what breaks. Every list and every
figure in the reports is therefore read through one of them, and the thing most
likely to go wrong is not the filter itself but the rows that predate it — the
`kind` column was added after these tables had history in them, and a filter
that matched the bare column would quietly hide every one of those runs from
both tabs. That is what most of this file is about.
"""

import time

import pytest

import storage


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "platforms.db"))
    monkeypatch.setattr(storage, "_schema_ready", False)
    storage.init_db()
    return storage


def make_run(kind, status="passed", when=None, priority=None, case_id=None):
    run_id = storage.create_run(
        goal=f"a {kind} run", platform=kind, kind=kind, case_id=case_id,
    )
    storage.finish_run(run_id, status)
    with storage._connect() as conn:
        if priority is not None:
            conn.execute("UPDATE runs SET case_priority = ? WHERE id = ?", (priority, run_id))
        if when is not None:
            conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (when, run_id))
    return run_id


def legacy_run(status="passed"):
    """A run from before the column existed: kind is NULL, and it was web."""
    run_id = storage.create_run(goal="an old run")
    storage.finish_run(run_id, status)
    with storage._connect() as conn:
        conn.execute("UPDATE runs SET kind = NULL WHERE id = ?", (run_id,))
    return run_id


# --- what counts as a platform --------------------------------------------- #

def test_only_the_two_platforms_filter_anything():
    assert storage.clean_kind("web") == "web"
    assert storage.clean_kind("MOBILE") == "mobile"
    # Anything else means "no filter" rather than "a platform nothing is on",
    # so a stray query string returns the list instead of an empty one.
    for value in (None, "", "  ", "all", "desktop", "web "):
        assert storage.clean_kind(value) in (None, "web"), value
    assert storage.clean_kind("all") is None


# --- the lists -------------------------------------------------------------- #

def test_a_platform_shows_its_own_runs_and_not_the_others(db):
    make_run("web")
    make_run("web")
    make_run("mobile")

    assert len(db.list_runs(kind="web")) == 2
    assert len(db.list_runs(kind="mobile")) == 1
    # No platform asked for is every platform, which is what the other callers
    # of list_runs — exports, reports, the suite runner — still want.
    assert len(db.list_runs()) == 3


def test_runs_from_before_the_column_existed_are_web(db):
    """They are the whole reason the comparison goes through COALESCE: web is
    all there was when they ran, and a bare `kind = 'web'` would leave them in
    neither tab."""
    legacy_run()
    make_run("mobile")

    assert len(db.list_runs(kind="web")) == 1
    assert len(db.list_runs(kind="mobile")) == 1
    assert db.platform_counts("runs") == {"web": 1, "mobile": 1}


def test_the_tab_counts_are_taken_over_everything_not_over_one_page(db):
    """Test Runs pages fifty at a time, so counting what is loaded would say
    "Mobile 0" until the tester had scrolled far enough to disprove it."""
    for _ in range(3):
        make_run("mobile")
    make_run("web")

    assert db.platform_counts("runs") == {"web": 1, "mobile": 3}
    assert len(db.list_runs(limit=2, kind="mobile")) == 2


def test_counts_are_refused_for_a_table_that_was_not_meant_to_have_them(db):
    """The table name goes into the SQL text, so the whitelist is the whole
    safety argument — it should fail loudly rather than interpolate."""
    with pytest.raises(ValueError):
        db.platform_counts("runs; DROP TABLE runs")


# --- the figures ------------------------------------------------------------ #

def test_the_trend_is_drawn_for_one_platform(db):
    make_run("web", "passed")
    make_run("web", "failed")
    make_run("mobile", "failed")

    web = db.trend(days=7, kind="web")
    assert sum(day["total"] for day in web) == 2
    assert sum(day["failed"] for day in web) == 1

    mobile = db.trend(days=7, kind="mobile")
    assert sum(day["total"] for day in mobile) == 1
    assert sum(day["passed"] for day in mobile) == 0


def test_a_platform_with_no_history_reports_nothing_rather_than_everything(db):
    """The failure that would matter: a filter that silently does not apply
    reads as "mobile is doing fine" when there is no mobile."""
    make_run("web", "failed")
    make_run("web", "failed")

    assert db.trend(days=7, kind="mobile") == []
    assert sum(band["total"] for band in db.priority_breakdown(7, kind="mobile")) == 0
    assert db.usage_totals(7, kind="mobile")["runs"] == 0


def test_the_priority_bands_are_counted_within_the_platform(db):
    make_run("web", "failed", priority="Critical")
    make_run("mobile", "failed", priority="Critical")

    web = {band["priority"]: band for band in db.priority_breakdown(7, kind="web")}
    assert web["Critical"]["total"] == 1
    assert web["Critical"]["failed"] == 1


def test_model_spend_is_attributed_to_the_platform_that_spent_it(db):
    storage.record_run_usage(make_run("mobile"), {"input_tokens": 100, "calls": 1})
    storage.record_run_usage(make_run("web"), {"input_tokens": 900, "calls": 4})

    assert db.usage_totals(7, kind="mobile")["input_tokens"] == 100
    assert db.usage_totals(7, kind="web")["input_tokens"] == 900
    assert db.usage_totals(7)["input_tokens"] == 1000


def test_flakiness_reads_the_platform_off_the_test_set(db):
    """A case belongs to one platform for its whole life, so it is read from
    the set rather than from the runs — a case whose runs all predate the
    column still belongs on the tab its set is on."""
    web_suite = db.create_suite("web set", kind="web")
    mobile_suite = db.create_suite("mobile set", kind="mobile")
    web_case = db.add_case(web_suite, "a web case", "g")
    mobile_case = db.add_case(mobile_suite, "a mobile case", "g")

    for case_id, status in [(web_case, "passed"), (web_case, "failed"),
                            (mobile_case, "passed"), (mobile_case, "failed")]:
        make_run("web", status, case_id=case_id)

    assert [row["case_id"] for row in db.flakiness_report(kind="web")] == [web_case]
    assert [row["case_id"] for row in db.flakiness_report(kind="mobile")] == [mobile_case]
    assert len(db.flakiness_report()) == 2


# --- bugs ------------------------------------------------------------------- #

def test_a_bug_takes_the_platform_of_the_run_it_was_raised_from(db):
    """Nothing in the UI asks which platform a bug is on, because the run it
    came from already knows — and a bug filed on the wrong tab is a bug the
    team that owns it never sees."""
    run_id = make_run("mobile", "failed")
    bug_id = db.create_bug(title="it broke", run_id=run_id)

    assert db.get_bug(bug_id)["kind"] == "mobile"
    assert [b["id"] for b in db.list_bugs(kind="mobile")] == [bug_id]
    assert db.list_bugs(kind="web") == []


def test_a_bug_raised_with_no_run_behind_it_is_web(db):
    assert db.get_bug(db.create_bug(title="typed by hand"))["kind"] == "web"


def test_the_status_counts_are_counted_within_the_platform(db):
    """The status filter sits under the platform tab, so "1 open" has to mean
    one open on this tab."""
    db.create_bug(title="a", run_id=make_run("web", "failed"))
    db.create_bug(title="b", run_id=make_run("mobile", "failed"))
    db.create_bug(title="c", run_id=make_run("mobile", "failed"), status="fixed")

    assert db.bug_counts("web") == {"open": 1, "all": 1}
    assert db.bug_counts("mobile") == {"open": 1, "fixed": 1, "all": 2}
    assert db.bug_counts()["all"] == 3
    assert db.platform_counts("bugs") == {"web": 1, "mobile": 2}


def test_bugs_raised_before_the_column_existed_keep_their_runs_platform(db):
    """The backfill, which runs once against a live database — without it every
    older bug would sit outside both tabs and simply not appear.

    A fresh database cannot show this: it gets the column NOT NULL from the
    schema. Only a database that predates it can, because there the column
    arrives by ALTER TABLE and every existing row gets a NULL. So the table is
    put back into that shape before the migration is asked to repair it.
    """
    run_id = make_run("mobile", "failed")
    with storage._connect() as conn:
        conn.execute("DROP TABLE bugs")
        conn.execute("""CREATE TABLE bugs (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                                           run_id TEXT, status TEXT NOT NULL DEFAULT 'open',
                                           created_at REAL NOT NULL)""")
        conn.execute("INSERT INTO bugs (id, title, run_id, created_at) VALUES (?, ?, ?, ?)",
                     ("old", "an old one", run_id, time.time()))
        conn.execute("ALTER TABLE bugs ADD COLUMN kind TEXT")
        assert conn.execute("SELECT kind FROM bugs").fetchone()["kind"] is None
        storage._backfill_bug_kind(conn)

    with storage._connect() as conn:
        assert conn.execute("SELECT kind FROM bugs").fetchone()["kind"] == "mobile"


def test_the_platform_filter_stacks_with_the_filters_beside_it(db):
    db.create_bug(title="open on web", run_id=make_run("web", "failed"))
    db.create_bug(title="fixed on web", run_id=make_run("web", "failed"), status="fixed")
    db.create_bug(title="open on mobile", run_id=make_run("mobile", "failed"))

    found = db.list_bugs(status="open", kind="web")
    assert [b["title"] for b in found] == ["open on web"]


def test_a_search_still_only_searches_the_platform_being_viewed(db):
    db.create_bug(title="search is broken", run_id=make_run("web", "failed"))
    db.create_bug(title="search is broken", run_id=make_run("mobile", "failed"))

    assert len(db.list_bugs(search="search", kind="web")) == 1
    assert len(db.list_bugs(search="search")) == 2


def test_an_old_run_and_a_recent_one_are_not_mixed_by_the_platform_filter(db):
    """The window and the platform are separate questions; filtering by one
    must not quietly widen the other."""
    make_run("web", when=time.time() - 40 * 86400)
    make_run("web")

    assert sum(day["total"] for day in db.trend(days=7, kind="web")) == 1
    assert sum(day["total"] for day in db.trend(days=60, kind="web")) == 2
