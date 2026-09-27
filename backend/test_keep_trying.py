"""assert_visible keeps looking, like every other check.

On the web the driver answers at once, so a recording's `click`, then
`assert_visible` on the page the click opens, only held with a `wait`
between them — and the recordings filled up with waits in front of checks
that should have waited by themselves.
"""

import asyncio

import agent
from drivers.base import ActionResult


class Target:
    """Says no until the `yes_at`th look."""

    def __init__(self, yes_at):
        self.yes_at = yes_at
        self.looks = 0

    async def act(self, kind, element_id, selector, value, snapshot_id):
        self.looks += 1
        seen = self.looks >= self.yes_at
        return ActionResult(seen, "seen" if seen else "not yet")


def test_assert_visible_looks_again_until_it_is_there(monkeypatch):
    # conftest turns the window off for the suite, where every screen is a
    # fixture that will never change; this one is about the window.
    monkeypatch.setattr(agent, "ASSERT_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(agent, "ASSERT_POLL_SECONDS", 0.01)
    target = Target(yes_at=3)
    result = asyncio.run(agent._execute_action(
        target, {"action": "assert_visible", "selector": "#flightItem_0"}, None))
    assert result["ok"], result
    assert target.looks == 3


def test_assert_visible_gives_up_at_the_deadline(monkeypatch):
    monkeypatch.setattr(agent, "ASSERT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(agent, "ASSERT_WAIT_SECONDS", 0.05)
    target = Target(yes_at=10_000)
    result = asyncio.run(agent._execute_action(
        target, {"action": "assert_visible", "selector": "#never"}, None))
    assert not result["ok"]
    assert result["message"] == "not yet"
    assert 2 <= target.looks < 20


def test_a_check_that_passes_at_once_costs_one_look(monkeypatch):
    target = Target(yes_at=1)
    result = asyncio.run(agent._execute_action(
        target, {"action": "assert_visible", "selector": "#here"}, None))
    assert result["ok"]
    assert target.looks == 1
