"""Getting a freshly attached device to the app's own first screen.

A session that has just been created is not yet somewhere a tester can work.
The app is still launching, the OS is about to ask about location, tracking and
notifications, and on iOS answering one of those can leave the home screen in
front. None of that is the agent's job — it is deterministic, costs no model
tokens, and has to happen before the first frame anyone sees.

Nothing here raises. A device that will not answer a question is a device the
tester should still get a session on; every helper degrades to "leave it as it
is" rather than failing the connect.
"""

import asyncio
import time
from typing import Any, Dict, Optional

import appium_client as appium

# Buttons that grant. Ordered most permissive first, because a location prompt
# offers "Allow Once" next to "Allow While Using App" and the wider grant is
# the one that keeps a test from being asked again. Matched case-insensitively
# against the alert's own buttons, so a deny — "Don't Allow", "İzin Verme" —
# can never be pressed by accident.
ALLOW_LABELS = (
    "Uygulamayı Kullanırken İzin Ver",
    "Allow While Using App",
    "Her Zaman İzin Ver",
    "Always Allow",
    "İzin Ver",
    "Allow",
    "Tamam",
    "OK",
    "Kabul Et",
    "Accept",
    "Devam",
    "Continue",
    "Evet",
    "Yes",
)

# Ids that are never a valid answer to "what is the app under test". Resolving
# one of these and then activating it is what pins a session to the home
# screen and leaves the tester unable to get back to their app.
SHELL_APP_IDS = {
    "com.apple.springboard",
    "com.apple.preferences",
    "com.android.launcher",
    "com.google.android.apps.nexuslauncher",
}

# How long to keep watching for prompts. A first launch can ask two or three
# things several seconds apart, so a single look — or a loop that gives up the
# first time it sees nothing — misses the one that matters.
DEFAULT_WINDOW_S = 12.0
POLL_S = 0.7
# Always watch at least this long. A cold app launch does not ask immediately —
# the location prompt on a first run lands somewhere around three seconds in —
# so a routine that gives up as soon as the screen looks quiet misses exactly
# the prompt it exists for.
MIN_WATCH_S = 4.0
# Having watched the minimum, stop once nothing has happened for this long.
# Without an early exit every connect pays the full window, and a device whose
# permissions were granted on an earlier session is the common case.
QUIET_S = 2.5


def _grant_button(buttons: list) -> Optional[str]:
    """The button on this alert that grants, or None if none of them do."""
    lowered = {str(b).strip().lower(): str(b) for b in buttons if b}
    for label in ALLOW_LABELS:
        match = lowered.get(label.lower())
        if match:
            return match
    return None


async def settle_permissions(
    session_id: str, platform: str, window_s: float = DEFAULT_WINDOW_S,
) -> int:
    """Answer every OS prompt that comes up, for as long as they keep coming.

    Returns how many were answered. The `autoAcceptAlerts` capability only
    covers alerts raised while a command is in flight, and a session that has
    just attached is not running commands — so the first-launch prompts, which
    are exactly the ones a tester hits, arrive unhandled.
    """
    handled = 0
    started = time.monotonic()
    deadline = started + window_s
    last_seen = started

    while time.monotonic() < deadline:
        buttons = await appium.alert_buttons(session_id, platform)
        has_alert = bool(buttons) or await appium.alert_text(session_id) is not None

        if has_alert:
            if await appium.accept_alert(session_id, _grant_button(buttons)):
                handled += 1
                last_seen = time.monotonic()
                # Prompts stack: the next one is often already behind this one.
                await asyncio.sleep(0.4)
                continue

        now = time.monotonic()
        if now - started >= MIN_WATCH_S and now - last_seen >= QUIET_S:
            break
        await asyncio.sleep(POLL_S)

    return handled


async def resolve_app_id(
    session_id: str,
    platform: str,
    requested: Optional[str],
    session_caps: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """What the app under test is actually called on the device.

    The tester picks a build, and on a cloud device that choice is an upload
    handle — `bs://<hash>` — which names nothing the OS understands. The real
    id has to come back from the session, or be read off whatever is now in
    front, before the app can be activated by name.
    """
    candidates = []
    caps = session_caps or {}
    for key in ("bundleId", "appPackage", "appium:bundleId", "appium:appPackage"):
        value = caps.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    if requested and not requested.startswith("bs://"):
        candidates.append(requested)
    live = await appium.active_app_info(session_id, platform)
    if live:
        candidates.append(live)

    for candidate in candidates:
        if candidate.strip().lower() not in SHELL_APP_IDS:
            return candidate
    return None


async def ensure_foreground(
    session_id: str, app_id: Optional[str], attempts: int = 3,
) -> bool:
    """Put the app in front, and keep checking until it is.

    Accepting a permission prompt can hand the screen back to the OS rather
    than to the app that asked, and a tester then has no way back — the mirror
    shows a home screen and the app cannot be reopened from it.
    """
    if not app_id:
        return False
    for attempt in range(attempts):
        state = await appium.query_app_state(session_id, app_id)
        if state == 4:
            return True
        # None means the driver would not answer. Activating anyway is the
        # safe move: it is a no-op for an app already in front.
        await appium.activate_app(session_id, app_id)
        if attempt < attempts - 1:
            await asyncio.sleep(0.8)
    return await appium.query_app_state(session_id, app_id) == 4


async def prepare_session(
    session_id: str,
    platform: str,
    requested_app_id: Optional[str],
    session_caps: Optional[Dict[str, Any]] = None,
    window_s: float = DEFAULT_WINDOW_S,
) -> Optional[str]:
    """Take a just-created session to the app's own first screen.

    Order matters. The prompts are answered first, because the id is read off
    what is in front and while an alert owns the screen that is the OS, not the
    app — resolving first is how a session ends up pinned to the home screen.
    Only then is the app confirmed to be in the foreground.

    Returns the resolved app id, so the rest of the system can name the app
    something better than an upload handle.
    """
    if platform.lower() == "ios":
        # Tells XCUITest to notice alerts that belong to the system rather than
        # the app, which is what a permission prompt is.
        await appium.update_settings(session_id, {"respectSystemAlerts": True})

    try:
        await settle_permissions(session_id, platform, window_s)
    except Exception as exc:
        print(f"[mobile] settling permissions failed: {exc}")

    try:
        app_id = await resolve_app_id(session_id, platform, requested_app_id, session_caps)
    except Exception as exc:
        print(f"[mobile] could not resolve the app id: {exc}")
        app_id = requested_app_id if requested_app_id and not requested_app_id.startswith("bs://") else None

    try:
        if app_id and not await ensure_foreground(session_id, app_id):
            print(f"[mobile] {app_id} would not come to the foreground")
    except Exception as exc:
        print(f"[mobile] could not foreground {app_id}: {exc}")

    return app_id
