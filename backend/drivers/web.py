"""Playwright-backed target: the same agent, driving a browser.

Web locators are far more stable than mobile ones — a CSS selector with an id
or a test id survives re-renders that would shift an XPath — so this driver
resolves against the snapshot's own selector and lets Playwright do the
waiting, instead of running the mobile side's semantic re-matching.
"""

import asyncio
import base64
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from config import ARTIFACT_DIR, AUTH_DIR, WEB_EXTRA_HEADERS
from web_dom import EXTRACT_JS, WebSnapshot

from .base import ActionResult, Snapshot

# Kept per session so a step can report what the page complained about while it
# ran. Bounded: a chatty page would otherwise grow this without limit.
MAX_PAGE_EVENTS = 200


# Runs before any page script. Chromium still leaves navigator.webdriver
# readable as false even with the launch flag; some bot-protection layers key on
# it, so it is hidden outright to match a plain browser.
_STEALTH_INIT = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"


async def apply_stealth(context) -> None:
    try:
        await context.add_init_script(_STEALTH_INIT)
    except Exception as exc:
        print(f"[web] could not install stealth init script: {exc}")


async def apply_extra_headers(context) -> None:
    """Install the configured per-domain request headers on a fresh context.

    Registered as a catch-all route so the match can be made on the request URL;
    Playwright's own extra-headers option is context-wide and would send a
    header meant for one host to every host the page touches.
    """
    if not WEB_EXTRA_HEADERS:
        return

    async def inject(route, request):
        extra: Dict[str, str] = {}
        for needle, headers in WEB_EXTRA_HEADERS.items():
            if needle in request.url:
                extra.update(headers)
        if extra:
            await route.continue_(headers={**request.headers, **extra})
        else:
            await route.continue_()

    await context.route("**/*", inject)


def auth_path(profile: str) -> str:
    """Where a named sign-in is stored. The name is sanitised because it
    arrives from the UI and ends up as a filename."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", (profile or "default").strip())[:60] or "default"
    return os.path.join(AUTH_DIR, f"{safe}.json")


def list_auth_profiles() -> List[Dict[str, Any]]:
    profiles = []
    for name in sorted(os.listdir(AUTH_DIR)) if os.path.isdir(AUTH_DIR) else []:
        if not name.endswith(".json"):
            continue
        full = os.path.join(AUTH_DIR, name)
        profiles.append({
            "name": name[:-5],
            "savedAt": os.path.getmtime(full),
            "sizeBytes": os.path.getsize(full),
        })
    return profiles


def delete_auth_profile(profile: str) -> bool:
    path = auth_path(profile)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def run_artifact_dir(run_id: str) -> str:
    path = os.path.join(ARTIFACT_DIR, re.sub(r"[^A-Za-z0-9_-]", "", run_id)[:32] or "run")
    os.makedirs(path, exist_ok=True)
    return path


# Far enough off any real desktop that the window is never composited onto a
# visible monitor.
OFFSCREEN_POSITION = "-32000,-32000"

# Asked for far outside any display. Every platform clamps this to what it will
# allow; the point is to end up as close to the corner as the window manager
# permits, not to land on these exact coordinates.
PARK_BOUNDS = {"left": 30000, "top": 30000, "width": 100, "height": 100}


# --- cookie consent -------------------------------------------------------- #
#
# The banner is a toll every scenario paid. It sits over the page from the first
# paint, and scenarios were written with "Close the cookie consent banner by
# tapping Accept." as step one or two — 49 of the 62 in the suite carry it. Each
# one costs a model call, a screenshot and the tokens to describe a screen that
# nobody is testing. It is the same click every time, on a control the page puts
# in the same place, which makes it the driver's job rather than the agent's.

# Id first, and text only as a fallback, because text is actively misleading
# here: the Turkish Airlines banner labels BOTH of its buttons "...kabul
# ediyorum" — accepting everything and accepting only what is required — so
# matching on "kabul" is a coin toss that silently changes what the rest of the
# run is testing.
CONSENT_ACCEPT_SELECTORS = (
    "#allowCookiesButton",            # turkishairlines.com
    "#onetrust-accept-btn-handler",   # OneTrust, which most of the rest use
)

# Reserved for a banner neither id matches. Deliberately narrow: the label has
# to say it accepts *everything*, which means an all-word and an accept-word
# together, in either order — "Tüm çerezleri kabul ediyorum", "Tümünü kabul
# et", "Accept all cookies". A bare "Accept" does not qualify: on a two-button
# banner it is as likely to be the restrictive choice, so it is left to the
# agent rather than guessed at.
_ALL = r"(?:t[üu]m\w*|b[üu]t[üu]n\w*|hepsin\w*|\ball\b)"
_ACCEPT = r"(?:kabul|onayla\w*|izin\s*ver\w*|accept|allow|agree)"
CONSENT_ACCEPT_TEXT = re.compile(
    rf"{_ALL}[^.!?]{{0,40}}?{_ACCEPT}|{_ACCEPT}[^.!?]{{0,40}}?{_ALL}",
    re.IGNORECASE,
)

# Checked against the matched label before clicking it. The all-word and the
# accept-word can both appear in a refusal — "Tümünü reddet", "Only accept
# what is necessary" — and clicking that silently changes what the rest of the
# run is testing, which is the failure this whole block exists to avoid.
CONSENT_REFUSAL_TEXT = re.compile(
    r"sadece|yaln[ıi]zca|zorunlu|gerekli|reddet\w*|reject|decline|refuse|deny"
    r"|necessary|essential|required",
    re.IGNORECASE,
)

# The banner is painted with the first render, so this is a check rather than a
# wait. Long enough for a late one, short enough that a page without a banner —
# every page after the first — does not pay for the lookup.
CONSENT_TIMEOUT_MS = 2500


async def dismiss_consent(page: Any) -> Optional[str]:
    """Accept the cookie banner, if this page has one. Returns what it clicked.

    Best-effort by construction: a page with no banner, a banner this does not
    recognise, and a click that loses a race with a re-render are all ordinary
    outcomes, not failures. Whatever is left standing the agent still handles
    the way it does today — this removes the common case, it does not replace
    the general one.
    """
    selector = ", ".join(CONSENT_ACCEPT_SELECTORS)
    try:
        button = page.locator(selector).first
        await button.wait_for(state="visible", timeout=CONSENT_TIMEOUT_MS)
        # Which of them matched, so the log names the button that was pressed
        # rather than the list it was looked up in.
        found = await button.get_attribute("id")
        await button.click(timeout=CONSENT_TIMEOUT_MS)
        return f"#{found}" if found else selector
    except Exception:
        pass

    try:
        button = page.get_by_role("button", name=CONSENT_ACCEPT_TEXT).first
        if await button.count():
            label = (await button.inner_text()).strip()
            if CONSENT_REFUSAL_TEXT.search(label):
                return None
            await button.click(timeout=CONSENT_TIMEOUT_MS)
            return label
    except Exception:
        pass
    return None


async def park_window(page: Any) -> None:
    """Get a headed browser window out of the user's way, after it exists.

    The page is watched inside QAi, which is fed by screenshots, so the window
    itself has nothing to show anyone — it is a by-product of sites that refuse
    a headless browser. macOS clamps a window back onto the screen no matter
    what --window-position asked for, so the window has to be moved once it is
    open, over CDP, where the clamp only applies at the edges.

    Two things this deliberately does not do. It does not minimise the window:
    a minimised window stops painting and the frames the panel needs stop with
    it. And it does not shrink the viewport — the page keeps rendering at the
    size the panel shows it at, because the viewport is an emulation override
    and has nothing to do with how big the window is.

    Best effort by design: a window that cannot be moved is untidy, not broken,
    and is never worth failing a launch over.
    """
    try:
        cdp = await page.context.new_cdp_session(page)
        window = await cdp.send("Browser.getWindowForTarget")
        await cdp.send(
            "Browser.setWindowBounds",
            {"windowId": window["windowId"], "bounds": dict(PARK_BOUNDS)},
        )
    except Exception as exc:
        print(f"[web] could not move the browser window aside: {exc}")


def _launch_args(headless: bool, offscreen: bool, browser_name: str) -> List[str]:
    """Extra Chromium flags for this launch.

    Some sites refuse a headless browser outright, so QAi falls back to a real
    windowed one. That fallback used to put an actual Chrome window on the
    user's desktop, which is startling and gets in the way — the page is
    supposed to live inside QAi. Positioning the window off-screen keeps the
    full rendering path and fingerprint of a real browser while leaving the
    desktop alone; the panel still shows it, because the panel is fed by
    screenshots rather than by the window itself.

    Only Chromium takes these flags, and only a headed launch has a window to
    move.

    macOS ignores the position flag — it clamps a new window back onto the
    screen, so on a Mac this alone leaves a full browser window sitting on top
    of the user's work. `park_window` does the actual moving there; the flag is
    kept because it is what works on Windows and Linux, before any window has
    been painted.
    """
    if browser_name != "chromium":
        return []
    args = [
        # Drops the "controlled by automated test software" banner and the
        # navigator.webdriver=true flag, so a headed window looks like a plain
        # browser and bot-protection layers (PerimeterX on some sites) stop
        # failing the app's own API calls behind an error dialog.
        "--disable-blink-features=AutomationControlled",
    ]
    if not headless and offscreen:
        args += [
            f"--window-position={OFFSCREEN_POSITION}",
            # An off-screen window is "occluded" as far as Chromium is concerned,
            # and it throttles or stops painting one — which would freeze the
            # screenshot stream the panel depends on.
            "--disable-features=CalculateNativeWinOcclusion",
        ]
    return args


def _append_event(sink: List[Dict[str, Any]], event: Dict[str, Any]) -> None:
    event.setdefault("at", time.time())
    sink.append(event)
    if len(sink) > MAX_PAGE_EVENTS:
        del sink[0 : len(sink) - MAX_PAGE_EVENTS]


def _network_level(resource_type: Optional[str], status: Optional[int]) -> str:
    """`error` only for a failure that is unambiguously the application's.

    Both levels are recorded either way; the level decides whether a run's
    verdict is allowed to turn on the event, and warnings are shown to the
    tester as notices instead.

    The line is drawn at who is at fault and how certainly:

      * 5xx — the server broke. Nothing else it could mean.
      * a net-level failure of the document itself — the page never loaded.
      * everything else, including every 4xx — a warning.

    4xx used to be an error whenever it hit a document, xhr, fetch or script,
    and that classification failed real runs constantly. A live airline site
    answers 4xx all through a perfectly good booking: 404 on a bot-protection
    script, 404 on a RUM beacon, 400 on a probe, 428 on an API that then
    retries with the token it was being asked for, 410 on a challenge-loader
    document. One measured run logged 34 such events while every one of its
    steps passed and the flights were actually booked. A 4xx is the server
    answering deliberately; whether that answer mattered to the feature is
    what the scenario's own assertions are for.
    """
    if status is not None:
        return "error" if status >= 500 else "warning"
    # No status means the request never completed. That only decides a verdict
    # when it was the page itself: a sub-resource that never arrived is the
    # same class of fact as one that arrived as a 404.
    return "error" if resource_type == "document" else "warning"


def _is_third_party(page, url: Optional[str]) -> bool:
    """Is this someone else's server? Their outages are not the test's problem."""
    if not url:
        return False
    try:
        target = urlparse(url).netloc.lower()
        origin = urlparse(page.url).netloc.lower()
    except Exception:
        return False
    if not target or not origin:
        return False
    # Treat sub-domains of the same registrable-ish suffix as first party:
    # cdn.example.com serving example.com is not a third party.
    return target.split(":")[0].split(".")[-2:] != origin.split(":")[0].split(".")[-2:]


def attach_page_listeners(page, sink: List[Dict[str, Any]]) -> None:
    """Subscribe to the page's own error channels, recording into `sink`.

    A run that clicks through happily while the console throws and an XHR
    returns 500 is not a passing run — but nothing in the screenshot says so.
    These are the signals a human tester would have devtools open for.

    This is a free function rather than a method because it has to be attached
    to the page *before* the first navigation: subscribing afterwards misses
    every error the page raised while it was loading, which is where most of
    them happen.
    """

    def on_console(message) -> None:
        if message.type not in ("error", "warning"):
            return
        location = message.location if isinstance(message.location, dict) else {}
        # Computed here too, not only for network events. Without it every
        # console error was recorded as first-party, so a Google Sign-In
        # widget, a TikTok pixel or an mPulse beacon complaining in someone
        # else's script counted against the run — which is the opposite of
        # what _is_third_party exists to prevent.
        # Advisory, never a verdict. Two thirds of what arrives here is the
        # browser narrating a network event already recorded ("Failed to load
        # resource: … 404"), counted a second time; the rest is the app's own
        # console.error, which real code uses for things as harmless as
        # "Provider's accounts list is empty." An uncaught exception — the one
        # console signal that is unambiguous — does not come through here at
        # all, it arrives as `pageerror` and stays an error.
        _append_event(sink, {
            "kind": "console",
            "level": "warning",
            "text": message.text[:2000],
            "url": location.get("url"),
            "thirdParty": _is_third_party(page, location.get("url")),
        })

    def on_page_error(error) -> None:
        _append_event(sink, {"kind": "pageerror", "level": "error", "text": str(error)[:2000]})

    def on_request_failed(request) -> None:
        failure = request.failure or ""
        # A navigation the user themselves cancelled is noise, not a defect.
        if "ERR_ABORTED" in failure:
            return
        _append_event(sink, {
            "kind": "requestfailed",
            "level": _network_level(request.resource_type, None),
            "text": failure[:500],
            "url": request.url[:500],
            "resourceType": request.resource_type,
            "thirdParty": _is_third_party(page, request.url),
        })

    def on_response(response) -> None:
        if response.status < 400:
            return
        try:
            resource_type = response.request.resource_type
        except Exception:
            resource_type = "other"
        _append_event(sink, {
            "kind": "httperror",
            "level": _network_level(resource_type, response.status),
            "text": f"HTTP {response.status} {response.status_text}"[:200],
            "url": response.url[:500],
            "status": response.status,
            "resourceType": resource_type,
            "thirdParty": _is_third_party(page, response.url),
        })

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)

# One page's snapshots, addressed by id, exactly like the mobile ring buffer.
SNAPSHOTS_PER_SESSION = 8

# Chromium serves its own error document when a site refuses the connection, so
# the navigation "succeeds" and only the URL gives it away. Treating that as a
# loaded page is worse than failing: the session opens on an error screen, the
# element tree is empty, and every later feature reports nonsense about it.
ERROR_PAGE_PREFIXES = ("chrome-error://", "about:blank", "edge-error://")

# The signature of a connection that opens and then dies without a response.
# On sites that fingerprint automated browsers this is intermittent — the same
# URL that fails now often loads on the next attempt — so it is worth retrying
# rather than reporting straight away.
TRANSIENT_SIGNATURES = (
    "ERR_HTTP2_PROTOCOL_ERROR", "ERR_SSL_PROTOCOL_ERROR", "ERR_CONNECTION_RESET",
    "ERR_QUIC_PROTOCOL_ERROR", "ERR_SPDY_PROTOCOL_ERROR", "ERR_EMPTY_RESPONSE",
    "ERR_CONNECTION_CLOSED", "ERR_NETWORK_CHANGED",
)

NAVIGATION_ATTEMPTS = 3
RETRY_DELAYS = (1.5, 3.5)


class NavigationError(RuntimeError):
    """A navigation that did not end on the page that was asked for."""


def is_error_page(url: Optional[str]) -> bool:
    return bool(url) and url.startswith(ERROR_PAGE_PREFIXES)


def looks_transient(message: str) -> bool:
    return any(signature in message for signature in TRANSIENT_SIGNATURES)


async def settle(page, timeout: int = 6000) -> None:
    """Give client-side rendering a moment. networkidle never fires on a site
    that polls, so the wait is capped rather than depended on."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass


async def goto_with_retry(page, url: str, attempts: int = NAVIGATION_ATTEMPTS) -> None:
    """Navigate, retrying while the failure looks like the intermittent kind.

    Raises NavigationError with the last reason if every attempt fails. Three
    failure modes are handled: goto() raising, goto() succeeding onto
    Chromium's error document, and — the one that slipped through — a page that
    passes the initial check and only then falls back to an error document
    while its scripts run. The verdict is therefore taken after the page has
    settled, not the instant navigation reports done.
    """
    last = "the page did not load"

    for attempt in range(attempts):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if not is_error_page(page.url):
                await settle(page)
                if not is_error_page(page.url):
                    return
                last = f"{url} loaded and then fell back to an error page"
            else:
                last = f"{url} returned an error page instead of content"
        except Exception as exc:
            last = str(exc).splitlines()[0]
            if not looks_transient(last):
                raise NavigationError(last) from exc

        if attempt < attempts - 1:
            await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])

    raise NavigationError(f"{last} (tried {attempts} times)")

VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900, "mobile": False},
    "tablet": {"width": 834, "height": 1112, "mobile": True},
    "mobile": {"width": 390, "height": 844, "mobile": True},
}


def _clamp(value: Optional[int], low: int, high: int) -> Optional[int]:
    if value is None:
        return None
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return None

KEY_MAP = {
    "back": "__history_back__",
    "forward": "__history_forward__",
    "enter": "Enter",
    "escape": "Escape",
    "tab": "Tab",
    "delete": "Backspace",
    "search": "Enter",
    "home": "Home",
    "end": "End",
}


class WebTarget:
    kind = "web"

    def __init__(
        self, session_id: str, playwright, browser, context, page,
        config: Dict[str, Any], events: Optional[List[Dict[str, Any]]] = None,
    ):
        self.session_id = session_id
        self._pw = playwright
        self._browser = browser
        self._context = context
        self.page = page
        self.config = config
        self._snapshots: List[WebSnapshot] = []
        # Listeners are attached before the first navigation, so this list may
        # already hold what the page complained about while it was loading.
        self._events: List[Dict[str, Any]] = events if events is not None else []
        self._tracing = False
        self._routes: List[Dict[str, Any]] = []
        # The screencast, when a viewer is watching. Chromium pushes a frame
        # every time the page paints, which is what makes the mirror move like
        # a browser rather than like a slideshow of polled screenshots.
        self._cdp = None
        self._screencasting = False
        self._frames: "Optional[asyncio.Queue]" = None
        self._frame_listener = False
        # Acks are fire-and-forget tasks, and a task nothing holds can be
        # collected before it runs. Chromium sends no further frame until the
        # last one is acknowledged, so a collected ack does not drop a frame —
        # it stops the stream. Measured: 14.8 fps, then 2.0, then 0.2 across
        # three viewers until these were kept.
        self._acks: set = set()

    # --- what the page complained about ---------------------------------- #

    def _record(self, event: Dict[str, Any]) -> None:
        _append_event(self._events, event)

    def _attach_listeners(self, page) -> None:
        attach_page_listeners(page, self._events)

    def drain_events(self) -> List[Dict[str, Any]]:
        """Hand over everything seen since the last call and start fresh."""
        events, self._events = self._events, []
        return events

    def peek_events(self) -> List[Dict[str, Any]]:
        return list(self._events)

    # --- network mocking -------------------------------------------------- #

    async def set_routes(self, rules: List[Dict[str, Any]]) -> int:
        """Install URL-pattern rules that fulfil or abort matching requests.

        Testing the error path — a 500 from the search API, a timeout on
        checkout — is impossible against a live backend that insists on
        working. Each rule is {url, status?, body?, contentType?, abort?}.
        """
        await self._context.unroute_all(behavior="ignoreErrors")
        self._routes = []
        # Cleared along with the mock rules, and re-installed first so the rules
        # below — registered later, therefore matched first — still win.
        await apply_extra_headers(self._context)

        for rule in rules or []:
            pattern = rule.get("url")
            if not pattern:
                continue
            self._routes.append(rule)

            async def handler(route, _rule=rule):
                if _rule.get("abort"):
                    await route.abort(_rule.get("abort") if isinstance(_rule.get("abort"), str) else "failed")
                    return
                body = _rule.get("body", "")
                if not isinstance(body, str):
                    body = json.dumps(body, ensure_ascii=False)
                await route.fulfill(
                    status=int(_rule.get("status", 200)),
                    content_type=_rule.get("contentType", "application/json"),
                    body=body,
                )

            await self._context.route(pattern, handler)

        return len(self._routes)

    def describe_routes(self) -> List[Dict[str, Any]]:
        return list(self._routes)

    # --- trace & video ---------------------------------------------------- #

    async def start_trace(self) -> bool:
        if self._tracing:
            return True
        try:
            await self._context.tracing.start(screenshots=True, snapshots=True, sources=False)
            self._tracing = True
        except Exception as exc:
            print(f"[web] tracing unavailable: {exc}")
        return self._tracing

    async def stop_trace(self, path: str) -> Optional[str]:
        """Write the trace to `path`. Returns the path, or None if there wasn't one."""
        if not self._tracing:
            return None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            await self._context.tracing.stop(path=path)
            self._tracing = False
            return path if os.path.exists(path) else None
        except Exception as exc:
            print(f"[web] could not write trace: {exc}")
            self._tracing = False
            return None

    async def video_path(self) -> Optional[str]:
        """Available only after the page is closed — Playwright finalises the
        file on close, so reading it earlier yields a truncated video."""
        try:
            video = self.page.video
            return await video.path() if video else None
        except Exception:
            return None

    # --- saved sign-in ---------------------------------------------------- #

    async def save_auth(self, profile: str) -> str:
        """Persist cookies and localStorage so later runs can skip the login.

        Logging in on every case is slow, brittle, and a good way to get an
        account rate-limited or locked.
        """
        path = auth_path(profile)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        await self._context.storage_state(path=path)
        return path

    # --- lifecycle ------------------------------------------------------- #

    @classmethod
    async def launch(
        cls,
        url: str,
        viewport: str = "desktop",
        headless: bool = True,
        browser_name: str = "chromium",
        width: Optional[int] = None,
        height: Optional[int] = None,
        auth_profile: Optional[str] = None,
        record_video_dir: Optional[str] = None,
        offscreen: bool = True,
        # Off only for a scenario that is about the banner itself. Nothing in
        # the suite is today — every scenario that mentions it is trying to get
        # past it — but a consent test would need the page as the user meets it.
        accept_consent: bool = True,
    ) -> "WebTarget":
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        browser = None
        try:
            launcher = getattr(playwright, browser_name, playwright.chromium)
            browser = await launcher.launch(
                headless=headless,
                args=_launch_args(headless, offscreen, browser_name),
            )
            spec = VIEWPORTS.get(viewport, VIEWPORTS["desktop"])
            # An explicit size wins over the preset: rendering the page at the
            # exact size of the panel it will be shown in means the screenshot
            # fills that panel with no letterboxing and no downscaling.
            size = {
                "width": _clamp(width, 320, 3840) or spec["width"],
                "height": _clamp(height, 320, 2160) or spec["height"],
            }
            context_options: Dict[str, Any] = {
                "viewport": size,
                "is_mobile": spec["mobile"] if browser_name == "chromium" else False,
                "has_touch": spec["mobile"],
                "locale": "tr-TR",
            }

            # A saved sign-in is loaded before the first navigation, so the very
            # first page already sees an authenticated session.
            if auth_profile:
                saved = auth_path(auth_profile)
                if os.path.exists(saved):
                    context_options["storage_state"] = saved
                else:
                    print(f"[web] auth profile '{auth_profile}' not found; starting signed out")
                    auth_profile = None

            if record_video_dir:
                os.makedirs(record_video_dir, exist_ok=True)
                context_options["record_video_dir"] = record_video_dir
                context_options["record_video_size"] = size

            context = await browser.new_context(**context_options)
            await apply_stealth(context)
            await apply_extra_headers(context)
            page = await context.new_page()
            page.set_default_timeout(15000)

            # Before the first navigation, so the window is already aside by
            # the time the site paints anything.
            if not headless and offscreen and browser_name == "chromium":
                await park_window(page)

            # Subscribe before navigating: a page that throws while loading —
            # which is most of them — would otherwise report a clean console.
            events: List[Dict[str, Any]] = []
            attach_page_listeners(page, events)

            # Handles every way this fails — goto() raising, goto() quietly
            # landing on Chromium's error document, and a page that only falls
            # back to one once its scripts run — waits for the page to settle,
            # and retries the kind of refusal that clears on its own.
            await goto_with_retry(page, url)

            # Before the agent is shown anything, so the first screenshot it
            # reasons about is the page rather than the page behind a banner.
            dismissed = await dismiss_consent(page) if accept_consent else None
            if dismissed:
                print(f"[web] accepted the cookie banner ({dismissed})")
        except Exception:
            # Close the browser too, not just the driver — a leaked chromium
            # process survives the failed attempt and holds its profile lock.
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            await playwright.stop()
            raise

        return cls(
            session_id=f"web-{uuid.uuid4().hex[:12]}",
            playwright=playwright,
            browser=browser,
            context=context,
            page=page,
            config={
                "url": url, "viewport": viewport, "browser": browser_name,
                "headless": headless, "width": size["width"], "height": size["height"],
                "authProfile": auth_profile, "videoDir": record_video_dir,
                "offscreen": offscreen,
                # Recorded so a report can say the banner was taken care of
                # before the run began, rather than leaving a reader to wonder
                # why a scenario's cookie step found nothing to close.
                "consentAccepted": bool(dismissed),
            },
            events=events,
        )

    def is_alive(self) -> bool:
        """Is the page still there?

        A browser that crashed or was closed keeps answering "no frame"
        forever, which the UI cannot tell apart from a slow screen.
        """
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    async def navigate(self, url: str) -> ActionResult:
        """Go to a page, the way a tester types an address.

        A scenario that has to reach a second page had no way to say so: the
        runner opens the browser at the case's URL and every action after that
        can only click what is already on screen. "Go back to the home page and
        start a new search" was unperformable, and the agent would hunt for a
        logo to click instead.

        Everything goes through here — the agent's `navigate` action and the
        address bar in the workspace. There were briefly two of these, and the
        later definition silently replaced the earlier one: the survivor took
        the URL raw, so a relative path like "/" — which the agent is told it
        may use — reached Chromium as an address and came back "Cannot navigate
        to invalid URL", killing the run rather than failing the step.
        """
        target = (url or "").strip()
        if not target:
            return ActionResult(False, "navigate needs a URL")
        if not target.startswith(("http://", "https://")):
            # A path is relative to where the session already is, which is what
            # "/tr-tr/flights" in a scenario means.
            current = urlparse(self.page.url)
            if target.startswith("/") and current.scheme and current.netloc:
                target = urlunparse((current.scheme, current.netloc, target, "", "", ""))
            else:
                target = "https://" + target
        try:
            # Not a bare goto(): a site that refuses the browser mid-session
            # leaves an error document behind without raising, exactly as it
            # does on the first load, and reporting that as a success is how a
            # session ends up sitting on "Bu siteye ulaşılamıyor".
            await goto_with_retry(self.page, target)
        except Exception as exc:
            return ActionResult(False, f"Could not open {target}: {exc}")
        # Every bound in them belongs to the page that just left.
        self._snapshots.clear()
        self.config["url"] = target
        return ActionResult(True, f"Opened {self.page.url}")

    async def reopen(self) -> Dict[str, Any]:
        """Bring a dead page back at the same URL, keeping the session id.

        Closing and re-opening from the UI would lose the session and the run
        history attached to it.
        """
        from playwright.async_api import async_playwright

        url = self.page.url if self.is_alive() else self.config.get("url")
        if not url or url.startswith("chrome-error://"):
            url = self.config.get("url")

        for closer in (self._context.close, self._browser.close, self._pw.stop):
            try:
                await closer()
            except Exception:
                pass

        playwright = await async_playwright().start()
        browser_name = self.config.get("browser", "chromium")
        launcher = getattr(playwright, browser_name, playwright.chromium)
        headless = self.config.get("headless", True)
        browser = await launcher.launch(
            headless=headless,
            args=_launch_args(headless, self.config.get("offscreen", True), browser_name),
        )
        size = {"width": self.config.get("width", 1440), "height": self.config.get("height", 900)}

        options: Dict[str, Any] = {"viewport": size, "locale": "tr-TR"}
        profile = self.config.get("authProfile")
        if profile and os.path.exists(auth_path(profile)):
            options["storage_state"] = auth_path(profile)

        context = await browser.new_context(**options)
        await apply_stealth(context)
        await apply_extra_headers(context)
        page = await context.new_page()
        page.set_default_timeout(15000)

        self._pw, self._browser, self._context, self.page = playwright, browser, context, page
        self._snapshots.clear()
        self._events.clear()
        self._tracing = False
        # The old page's listeners died with it; without re-subscribing, the
        # reopened page reports no console or network errors at all.
        self._attach_listeners(page)
        if self._routes:
            await self.set_routes(self._routes)

        await goto_with_retry(page, url)
        self.config["url"] = url
        return {"url": page.url, "title": await page.title()}

    async def set_viewport(self, width: int, height: int) -> Dict[str, int]:
        """Re-render the page at a new size, so it keeps filling its panel."""
        size = {
            "width": _clamp(width, 320, 3840) or self.config.get("width", 1440),
            "height": _clamp(height, 320, 2160) or self.config.get("height", 900),
        }
        await self.page.set_viewport_size(size)
        self.config.update(size)
        self._snapshots.clear()  # every bound in them is now stale
        return size

    async def close(self) -> None:
        await self.stop_screencast()
        # An unstopped trace is discarded when the context goes, so drop it
        # rather than leaving the recorder running into a closed browser.
        if self._tracing:
            try:
                await self._context.tracing.stop()
            except Exception:
                pass
            self._tracing = False

        for closer in (self._context.close, self._browser.close, self._pw.stop):
            try:
                await closer()
            except Exception:
                pass
        self._snapshots.clear()
        self._events.clear()

    def describe(self) -> Dict[str, Any]:
        return {
            "kind": "web",
            "platform": "Web",
            "name": self.config.get("url"),
            "udid": self.config.get("browser", "chromium"),
            "appId": self.config.get("url"),
        }

    # --- reading --------------------------------------------------------- #

    async def snapshot(self) -> Optional[Snapshot]:
        try:
            payload = await self.page.evaluate(EXTRACT_JS)
        except Exception as exc:
            print(f"[web] snapshot failed: {exc}")
            return None

        snapshot = WebSnapshot(payload)
        self._snapshots.append(snapshot)
        if len(self._snapshots) > SNAPSHOTS_PER_SESSION:
            del self._snapshots[0 : len(self._snapshots) - SNAPSHOTS_PER_SESSION]
        return snapshot

    def _find_snapshot(self, snapshot_id: Optional[str]) -> Optional[WebSnapshot]:
        if snapshot_id:
            for snapshot in reversed(self._snapshots):
                if snapshot.snapshot_id == snapshot_id:
                    return snapshot
            return None
        return self._snapshots[-1] if self._snapshots else None

    async def screenshot(self) -> Optional[str]:
        """An exact frame. PNG because visual regression compares this pixel
        for pixel: JPEG's compression artifacts alone differ on 2.2% of pixels,
        eleven times the default 0.2% tolerance, so every baseline check would
        fail on the codec. The agent loop wants speed rather than exactness and
        uses `mirror_frame` instead.
        """
        try:
            data = await self.page.screenshot(type="png")
            return base64.b64encode(data).decode()
        except Exception as exc:
            print(f"[web] screenshot failed: {exc}")
            return None

    # --- the live mirror ------------------------------------------------- #

    async def start_screencast(
        self,
        queue: "asyncio.Queue",
        quality: int = 85,
        max_width: Optional[int] = None,
        max_height: Optional[int] = None,
    ) -> bool:
        """Have Chromium push a frame whenever the page paints.

        Polling for screenshots costs a full capture per frame whether or not
        anything changed, and while an agent is driving the page it falls to
        two frames a step — which is why the mirror read as a slideshow. This
        is the mechanism devtools' own device preview uses: frames arrive as
        the page paints, around fifteen a second while something moves.

        Returns False when the browser has no CDP (a non-Chromium engine), and
        the caller falls back to polling.
        """
        self._frames = queue
        if self._screencasting:
            return True
        try:
            if self._cdp is None:
                self._cdp = await self._context.new_cdp_session(self.page)
        except Exception as exc:
            print(f"[web] screencast unavailable: {exc}")
            self._frames = None
            return False

        cdp = self._cdp

        async def ack(session_id: Any) -> None:
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass

        def on_frame(params: Dict[str, Any]) -> None:
            session = params.get("sessionId")
            if session is not None:
                # Chromium sends nothing further until each frame is
                # acknowledged, so this happens even for one about to be
                # dropped for being late — and the task is held until it has,
                # or it can be collected mid-flight and stall the stream.
                task = asyncio.create_task(ack(session))
                self._acks.add(task)
                task.add_done_callback(self._acks.discard)
            queue_now = self._frames
            data = params.get("data")
            if queue_now is None or not data:
                return
            try:
                queue_now.put_nowait(data)
            except asyncio.QueueFull:
                # A viewer that has fallen behind wants the newest frame, not a
                # backlog of stale ones, so the oldest goes.
                try:
                    queue_now.get_nowait()
                    queue_now.put_nowait(data)
                except Exception:
                    pass

        # Registered once for the life of the target, reading the current queue
        # each time rather than closing over one: removing and re-adding a CDP
        # listener between viewers failed silently, and every frame after the
        # first viewer left went to a queue nobody was reading.
        if not self._frame_listener:
            cdp.on("Page.screencastFrame", on_frame)
            self._frame_listener = True

        size = self.config or {}
        try:
            await cdp.send("Page.startScreencast", {
                "format": "jpeg",
                "quality": max(30, min(int(quality), 95)),
                "maxWidth": int(max_width or size.get("width") or 1440),
                "maxHeight": int(max_height or size.get("height") or 900),
                "everyNthFrame": 1,
            })
        except Exception as exc:
            print(f"[web] could not start the screencast: {exc}")
            self._frames = None
            return False
        self._screencasting = True
        return True

    async def stop_screencast(self) -> None:
        """Stop the push and drop the CDP session with it.

        Restarting a screencast on a session that has already run one does not
        resume: measured across four viewers on the same session it fell 14.2
        fps, 1.5, 0.2, 0.2. A session costs a millisecond to make, so each
        viewer gets a fresh one rather than inheriting whatever state the last
        one left behind.
        """
        self._frames = None
        cdp, self._cdp = self._cdp, None
        self._frame_listener = False
        if not self._screencasting or cdp is None:
            return
        self._screencasting = False
        try:
            await cdp.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            await cdp.detach()
        except Exception:
            pass

    async def mirror_frame(self) -> Optional[str]:
        """A fast frame for the live panel: JPEG straight from Chromium, no
        re-encoding. A PNG capture plus a Pillow re-shrink cost ~1.7s per frame
        and capped the mirror near 1fps; this is ~15x cheaper, which is what
        lets the panel keep up with the page under the user's hand.
        """
        try:
            data = await self.page.screenshot(type="jpeg", quality=72)
            return base64.b64encode(data).decode()
        except Exception as exc:
            print(f"[web] mirror frame failed: {exc}")
            return None

    async def element_at(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        snapshot = self._find_snapshot(None) or await self.snapshot()
        if snapshot is None:
            return None

        best, best_area = None, None
        for element in snapshot.get_all_elements():
            b = element.bounds
            if b["x1"] <= x <= b["x2"] and b["y1"] <= y <= b["y2"]:
                area = max(b["width"], 1) * max(b["height"], 1)
                if best_area is None or area < best_area:
                    best, best_area = element, area

        if best is None:
            return None
        payload = best.to_dict(include_children=False)
        payload["elementId"] = best.element_id
        return payload

    # --- acting ---------------------------------------------------------- #

    async def scroll(self, direction: str, element_id: Optional[str] = None) -> ActionResult:
        direction = (direction or "down").lower()
        deltas = {
            "down": (0, 0.8), "up": (0, -0.8),
            "right": (0.8, 0), "left": (-0.8, 0),
        }
        if direction not in deltas:
            return ActionResult(False, f"Unknown scroll direction '{direction}'")
        dx, dy = deltas[direction]

        box = None
        if element_id:
            snapshot = self._find_snapshot(None)
            element = snapshot.elements_by_id.get(element_id) if snapshot else None
            if element is None:
                return ActionResult(False, f"Could not find the scroll container '{element_id}'")
            try:
                box = await self.page.locator(element.selector).first.bounding_box()
            except Exception:
                box = None
            if box is None:
                return ActionResult(False, f'"{element.describe()}" is not visible to scroll within')

        try:
            if box:
                # The wheel scrolls whatever is under the pointer, so hovering
                # the container first is what keeps a dropdown or a picker off
                # the page behind it, which a page-wide scroll would hit instead.
                await self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                width, height = box["width"], box["height"]
            else:
                size = self.page.viewport_size or {"width": 1440, "height": 900}
                width, height = size["width"], size["height"]
            await self.page.mouse.wheel(dx * width, dy * height)
            await asyncio.sleep(0.4)
        except Exception as exc:
            return ActionResult(False, f"Scroll {direction} failed: {exc}")
        return ActionResult(True, f"Scrolled {direction}")

    # --- raw pointer, for hands-on control of the page ------------------- #
    #
    # The panel used to send a completed click no matter how the user actually
    # pressed: hold the mouse for five seconds and the page still received an
    # instant click. Splitting press from release makes the panel faithful —
    # what the user does with their mouse is what the page receives, live —
    # which is what any hold-to-confirm control, drag handle or long-press
    # menu needs in order to be testable by hand at all.

    async def pointer_down(self, x: int, y: int) -> ActionResult:
        try:
            await self.page.mouse.move(x, y)
            await self.page.mouse.down()
        except Exception as exc:
            return ActionResult(False, f"Pointer down failed: {exc}")
        return ActionResult(True, f"Pressed at ({x}, {y})")

    async def pointer_move(self, x: int, y: int) -> ActionResult:
        try:
            await self.page.mouse.move(x, y)
        except Exception as exc:
            return ActionResult(False, f"Pointer move failed: {exc}")
        return ActionResult(True, f"Moved to ({x}, {y})")

    async def pointer_up(self, x: Optional[int] = None, y: Optional[int] = None) -> ActionResult:
        try:
            if x is not None and y is not None:
                await self.page.mouse.move(x, y)
            await self.page.mouse.up()
        except Exception as exc:
            return ActionResult(False, f"Pointer up failed: {exc}")
        return ActionResult(True, "Released")

    async def press_key(self, key: str) -> ActionResult:
        mapped = KEY_MAP.get((key or "").lower())
        if mapped is None:
            return ActionResult(False, f"Key '{key}' is not supported on the web target")

        try:
            if mapped == "__history_back__":
                await self.page.go_back(wait_until="domcontentloaded")
                return ActionResult(True, "Navigated back")
            if mapped == "__history_forward__":
                await self.page.go_forward(wait_until="domcontentloaded")
                return ActionResult(True, "Navigated forward")
            await self.page.keyboard.press(mapped)
        except Exception as exc:
            return ActionResult(False, f"Key '{key}' failed: {exc}")
        return ActionResult(True, f"Pressed {key}")

    async def act(
        self,
        kind: str,
        element_id: Optional[str],
        selector: Optional[str],
        value: Optional[str],
        snapshot_id: Optional[str],
    ) -> ActionResult:
        snapshot = self._find_snapshot(snapshot_id)
        element = None

        if element_id and snapshot is not None:
            element = snapshot.elements_by_id.get(element_id)
        if element is None and element_id and snapshot_id:
            return ActionResult(
                False,
                f"Snapshot {snapshot_id} is no longer held; re-read the page and retry.",
            )

        target_selector = (element.selector if element else None) or selector
        if not target_selector:
            return ActionResult(False, f"Action '{kind}' needs an elementId")

        label = element.describe() if element else target_selector
        info = None
        if element is not None:
            info = {
                "id": element.resource_id,
                "text": element.text,
                "content-desc": element.name,
                "role": element.role,
                "xpath": element.selector,
                "href": element.href,
                "bounds": element.bounds,
                "label": label,
            }

        try:
            locator_handle = self.page.locator(target_selector).first
            count = await locator_handle.count()
            if count == 0:
                return ActionResult(False, f'"{label}" is no longer on the page.')

            if kind == "assert_visible":
                visible = await locator_handle.is_visible()
                return ActionResult(
                    visible,
                    f'"{label}" is visible' if visible else f'"{label}" is on the page but not visible',
                    info,
                )

            await locator_handle.scroll_into_view_if_needed(timeout=8000)

            if kind == "click":
                await locator_handle.click(timeout=12000)
                message = f'Clicked "{label}"'
            elif kind == "type":
                await locator_handle.fill(value or "", timeout=12000)
                message = f'Typed "{value}" into "{label}"'
            elif kind == "clear":
                await locator_handle.fill("", timeout=12000)
                message = f'Cleared "{label}"'
            else:
                return ActionResult(False, f"Unsupported action: {kind}")

            # A click often navigates; give the new document a moment to arrive.
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=6000)
            except Exception:
                pass
            await asyncio.sleep(0.3)
            return ActionResult(True, message, info)

        except Exception as exc:
            detail = str(exc).split("\n")[0][:220]
            return ActionResult(False, f'Could not {kind} "{label}": {detail}', info)
