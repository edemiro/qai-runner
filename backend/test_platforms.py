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


def test_an_execution_assembled_by_hand_takes_the_phone_of_its_cases(db):
    """Reported from the UI: iOS executions appeared under Android as well.
    A hand-picked execution has no Test Set of its own to read the OS off, so
    it carried none — and one that does not say which phone is shown on both
    tabs rather than neither, which is how an iOS run turned up under Android.
    """
    suite = db.create_suite("an iOS set", kind="mobile", os="ios")
    case_id = db.add_case(suite, "a scenario", "do it")
    picked = db.cases_by_id([case_id])
    assert picked[0]["suite_os"] == "ios"


def test_cases_from_two_phones_claim_neither(db):
    """A run mixing an iOS set with an Android one belongs to neither, and
    claiming one would file its report under a platform half of it never
    touched."""
    ios = db.add_case(db.create_suite("ios", kind="mobile", os="ios"), "a", "g")
    android = db.add_case(db.create_suite("and", kind="mobile", os="android"), "b", "g")
    found = {c["id"]: c["suite_os"] for c in db.cases_by_id([ios, android])}
    assert set(found.values()) == {"ios", "android"}


def test_an_execution_without_an_os_is_given_one_from_its_sources(db):
    """The backfill, for the executions already recorded without it."""
    suite = db.create_suite("an iOS set", kind="mobile", os="ios")
    run_id = db.create_suite_run(suite, 1, name="an execution", kind="mobile")
    with storage._connect() as conn:
        conn.execute("UPDATE suite_runs SET os = NULL WHERE id = ?", (run_id,))
        storage._backfill_execution_os(conn)
        row = conn.execute("SELECT os FROM suite_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["os"] == "ios"


# --- and within Mobile, which phone ----------------------------------------- #
#
# An iPhone run and a Pixel run are different apps with different selectors and
# different failure modes. Read in one list a failure on one looked like a
# failure on both, so everything below Mobile is split again — off the run's own
# `platform`, which is what the driver reported when it ran.

def phone_run(os_name, status="passed", case_id=None, goal="a phone run"):
    run_id = storage.create_run(
        goal=goal, platform=os_name, kind="mobile", case_id=case_id,
    )
    storage.finish_run(run_id, status)
    return run_id


def test_only_the_two_phones_narrow_anything(db):
    assert storage.clean_os("iOS") == "ios"
    assert storage.clean_os("android") == "android"
    # Anything else means "no filter" rather than "a phone nothing is on".
    for value in (None, "", "  ", "all", "windows"):
        assert storage.clean_os(value) is None, value
    # A web row never carries one, whatever was asked for.
    assert storage.clean_os("ios", kind="web") is None


def test_a_phone_shows_its_own_runs_and_not_the_others(db):
    ios = phone_run("iOS")
    android = phone_run("Android")
    web = make_run("web")

    assert [r["id"] for r in db.list_runs(kind="mobile", os="ios")] == [ios]
    assert [r["id"] for r in db.list_runs(kind="mobile", os="android")] == [android]
    assert sorted(r["id"] for r in db.list_runs(kind="mobile")) == sorted([ios, android])
    assert [r["id"] for r in db.list_runs(kind="web")] == [web]


def test_the_phone_counts_are_taken_over_everything_not_over_one_page(db):
    """The sub-tabs sit above a list that pages fifty at a time, so their
    numbers cannot come from the page that happens to be loaded."""
    for _ in range(3):
        phone_run("iOS")
    phone_run("Android")
    make_run("web")

    assert db.run_os_counts() == {"ios": 3, "android": 1}


def test_a_search_narrows_the_phone_counts_with_it(db):
    """Otherwise the tab says 3 and the list under it shows 1, and the two
    numbers read as a contradiction."""
    phone_run("iOS", goal="search a one way flight")
    phone_run("iOS", goal="check in")
    phone_run("Android", goal="search a one way flight")

    assert db.run_os_counts("one way") == {"ios": 1, "android": 1}


def test_the_trend_and_the_spend_are_split_by_phone_too(db):
    phone_run("iOS")
    phone_run("iOS", "failed")
    phone_run("Android")

    ios = db.trend(kind="mobile", os="ios")
    assert sum(day["total"] for day in ios) == 2
    android = db.trend(kind="mobile", os="android")
    assert sum(day["total"] for day in android) == 1
    assert sum(day["total"] for day in db.trend(kind="mobile")) == 3


def test_flakiness_reads_the_phone_off_the_test_set(db):
    """Same reasoning as the platform above it: a set belongs to one phone for
    its whole life, and a set written before the column existed has not said
    which — so it shows on both rather than vanishing from each."""
    ios_suite = db.create_suite("iOS set", kind="mobile", os="ios")
    android_suite = db.create_suite("Android set", kind="mobile", os="android")
    old_suite = db.create_suite("an older set", kind="mobile")
    cases = {
        name: db.add_case(suite, name, "g")
        for name, suite in [("ios", ios_suite), ("android", android_suite),
                            ("old", old_suite)]
    }
    for case_id in cases.values():
        phone_run("iOS", "passed", case_id=case_id)
        phone_run("iOS", "failed", case_id=case_id)

    on_ios = {row["case_id"] for row in db.flakiness_report(kind="mobile", os="ios")}
    assert on_ios == {cases["ios"], cases["old"]}
    on_android = {row["case_id"] for row in db.flakiness_report(kind="mobile", os="android")}
    assert on_android == {cases["android"], cases["old"]}


def test_a_bug_is_filed_under_the_phone_its_run_was_on(db):
    ios_bug = db.create_bug(title="ios", run_id=phone_run("iOS", "failed"))
    android_bug = db.create_bug(title="android", run_id=phone_run("Android", "failed"))

    assert [b["id"] for b in db.list_bugs(kind="mobile", os="ios")] == [ios_bug]
    assert [b["id"] for b in db.list_bugs(kind="mobile", os="android")] == [android_bug]
    assert db.bug_os_counts() == {"ios": 1, "android": 1}


def test_a_bug_filed_by_hand_stays_on_both_phones(db):
    """It has no run and so was never asked which phone. Hiding it behind a
    question it predates is how a finding goes missing from every tab."""
    by_hand = db.create_bug(title="typed by hand", kind="mobile")

    assert by_hand in [b["id"] for b in db.list_bugs(kind="mobile", os="ios")]
    assert by_hand in [b["id"] for b in db.list_bugs(kind="mobile", os="android")]


def test_the_status_counts_follow_the_phone_tab(db):
    db.create_bug(title="a", run_id=phone_run("iOS", "failed"))
    db.create_bug(title="b", run_id=phone_run("Android", "failed"))
    db.create_bug(title="c", run_id=phone_run("Android", "failed"), status="fixed")

    assert db.bug_counts("mobile", "ios") == {"open": 1, "all": 1}
    assert db.bug_counts("mobile", "android") == {"open": 1, "fixed": 1, "all": 2}


def test_the_web_tab_is_untouched_by_a_phone_asked_for_by_mistake(db):
    """A stray `&os=ios` on the Web tab must return the web runs, not none."""
    web = make_run("web")
    phone_run("iOS")

    assert [r["id"] for r in db.list_runs(kind="web", os="ios")] == [web]
