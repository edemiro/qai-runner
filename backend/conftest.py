"""Test-wide fixtures.

The only thing here is the database, and it matters more than it looks. The
suite creates runs, suites, cases and page events as fixtures, and `storage`
resolves its path once at import — so before this, every `pytest` run wrote its
fixtures into the working database. 75 of the 133 runs in it were scenarios
called "senaryo", which then counted in the pass rate on Insights, in the flaky
table and in every report drawn from run history.
"""

import os
import tempfile

import pytest

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
