"""Test-wide fixtures.

The database matters more than it looks. The suite creates runs, suites, cases
and page events as fixtures, and `storage` resolves its path once at import —
so before this, every `pytest` run wrote its fixtures into the working
database. 75 of the 133 runs in it were scenarios called "senaryo", which then
counted in the pass rate on Insights, in the flaky table and in every report
drawn from run history.
"""

import os
import tempfile

import pytest

import agent
import storage


@pytest.fixture(scope="session", autouse=True)
def _isolated_database():
    """Point storage at a throwaway file for the whole session.

    Session-scoped and autouse: no test should have to remember to ask for
    this, and a per-test database would cost a schema build per test for no
    benefit — the suite already creates what it needs.
    """
    handle, path = tempfile.mkstemp(prefix="qai-test-", suffix=".db")
    os.close(handle)

    original = storage.DB_PATH
    storage.DB_PATH = path
    storage.init_db()
    try:
        yield path
    finally:
        storage.DB_PATH = original
        try:
            os.unlink(path)
        except OSError:
            # Windows keeps a handle open a moment after the last connection
            # closes; a leftover file in the temp directory is not worth
            # failing a green suite over.
            pass


@pytest.fixture(autouse=True)
def _assertions_do_not_wait():
    """One reading of the screen per assertion, unless a test asks otherwise.

    Against a real page an assertion keeps looking for a few seconds, because
    a page that renders when its API answers is not finished when the click
    is. A fake target is finished the moment it is asked, so that window is
    pure sitting still — it put 42 seconds on a 24-second suite, all of it
    waiting for screens that were never going to change. Tests about the
    waiting set the constant themselves.
    """
    original = agent.ASSERT_WAIT_SECONDS
    agent.ASSERT_WAIT_SECONDS = 0.0
    try:
        yield
    finally:
        agent.ASSERT_WAIT_SECONDS = original


@pytest.fixture(autouse=True)
def _the_first_screen_is_already_there():
    """A fake target answers the moment it is asked.

    A run waits for its first screen before spending a model call on it, which
    against a real page is a second or two and against a fixture is the whole
    window, once per test that starts a run.
    """
    original = agent.FIRST_SCREEN_SECONDS
    agent.FIRST_SCREEN_SECONDS = 0.0
    try:
        yield
    finally:
        agent.FIRST_SCREEN_SECONDS = original
