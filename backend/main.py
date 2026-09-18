"""QAi backend — AI-driven mobile QA automation.

Routes only; the work lives in the focused modules alongside this file:
  config.py            environment, absolute paths
  appium_client.py     async Appium/W3C REST client
  process_manager.py   cross-platform Appium server lifecycle
  devices.py           adb / libimobiledevice discovery
  mobile_dom.py        XML -> semantic element tree
  locator.py           auto-wait, semantic re-matching, snapshots
  gesture_controller.py low-level gestures and key events
  agent.py             the autonomous agent loop
  explorer.py          deterministic exploratory crawl, link and a11y scans
  suggestions.py       page-derived test suggestions
  scenario_writer.py   scenarios written to the Digital Channels standard
  healing.py           self-healing step resolution for replay
  storage.py           run/step persistence
  exporters.py         run -> runnable test script
  reporters.py         run -> JUnit XML / JSON for CI
  suite_runner.py      suites, tags, data-driven and parallel execution
  visual.py            screenshot baselines and diffing
  cli.py               headless entry point for CI
"""

import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

import agent
import appium_client as appium
import browserstack
import config
import devices as device_discovery
import drivers
import explorer
import exporters
import healing
import locator
import process_manager
import reporters
import storage
import scenario_writer
import suggestions
import suite_runner
import visual
from config import ALLOWED_ORIGINS, ARTIFACT_DIR, WDA_BUNDLE_ID
from drivers import MobileTarget, WebTarget
from drivers import web as web_driver
from drivers.web import (
    delete_auth_profile,
    list_auth_profiles,
    run_artifact_dir,
)
from gesture_controller import MobileGestureController
from llm import ProviderError
from llm import registry as providers

# session_id -> platformName. Populated lazily, cheap to rebuild.
platform_cache: Dict[str, str] = {}


def require_target(session_id: str):
    target = drivers.get(session_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such session. Connect a device or open a page first.")
    return target


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.init_db()
    yield
    await drivers.close_all()
    await appium.close_client()


app = FastAPI(title="QAi API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Health & settings
# --------------------------------------------------------------------------- #

class ProviderConfig(BaseModel):
    provider: str
    model: Optional[str] = None
    api_key: Optional[str] = None
    # Provider-specific extra setting (currently the Anthropic workspace id).
    extra: Optional[str] = None


class ProviderTestRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    extra: Optional[str] = None


@app.get("/api/health")
async def health_check():
    state = providers.status()
    if not state["configured"]:
        return {
            "status": "warning",
            "llm_connected": False,
            **state,
            "message": f"No API key saved for {state['providerLabel']}. Add one in Settings.",
        }
    return {
        "status": "healthy",
        "llm_connected": True,
        **state,
        "message": f"Using {state['providerLabel']} · {state['model']}.",
    }


@app.get("/api/settings/providers")
async def list_providers():
    """The provider catalogue plus which one is active."""
    return {"providers": providers.catalog(), "active": providers.status()}


@app.get("/api/settings/providers/{provider_id}/models")
async def list_provider_models(provider_id: str):
    """Models this key can actually reach.

    Providers retire models, so a hardcoded list eventually recommends
    something the key is refused for. Asking the provider avoids that.
    """
    try:
        provider = providers.get(provider_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    key = providers.api_key_for(provider_id)
    if not key:
        raise HTTPException(status_code=400, detail=f"Save an API key for {provider.label} first.")

    lister = getattr(provider, "list_models", None)
    if lister is None:
        return {"models": [m["id"] for m in providers.suggested_models(provider_id)], "live": False}

    try:
        return {"models": await lister(key), "live": True}
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not list models: {exc}")


@app.post("/api/settings/provider")
async def save_provider(req: ProviderConfig):
    try:
        providers.save(req.provider, req.model, req.api_key, req.extra)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {exc}")
    return {"status": "success", "message": "Configuration saved.", "active": providers.status()}


@app.post("/api/settings/test")
async def test_provider(req: ProviderTestRequest):
    provider_id = req.provider or providers.active_provider_id()
    try:
        provider = providers.get(provider_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    key = (req.api_key or "").strip() or providers.api_key_for(provider_id)
    if not key:
        raise HTTPException(status_code=400, detail=f"No API key provided for {provider.label}.")

    model = (req.model or "").strip() or providers.default_model(provider_id)
    loop = asyncio.get_running_loop()
    start = loop.time()

    # Honour a not-yet-saved extra setting, so "Test connection" validates what
    # is on screen rather than what was saved last time.
    with providers.temporary_extra(provider_id, req.extra):
        try:
            snippet = await provider.check(model, key)
        except ProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{provider.label} validation failed: {exc}")

    return {
        "status": "success",
        "message": f"{provider.label} responded.",
        "latency": round(loop.time() - start, 2),
        "response_snippet": snippet,
    }


# --------------------------------------------------------------------------- #
# Appium server lifecycle
# --------------------------------------------------------------------------- #

@app.get("/api/appium/status")
async def get_appium_status():
    running = await appium.is_server_running()
    process_running = process_manager.is_process_alive()
    return {
        "running": running,
        "process_running": process_running,
        "status": "running" if running else ("starting" if process_running else "stopped"),
    }


@app.post("/api/appium/start")
async def start_appium_server():
    if await appium.is_server_running():
        return {"status": "success", "message": "Appium is already running on port 4723."}
    ok, message = await asyncio.to_thread(process_manager.start)
    if not ok:
        raise HTTPException(status_code=500, detail=message)
    return {"status": "success", "message": message}


@app.post("/api/appium/stop")
async def stop_appium_server():
    ok, message = await asyncio.to_thread(process_manager.stop)
    if not ok:
        raise HTTPException(status_code=500, detail=message)
    return {"status": "success", "message": message}


@app.get("/api/appium/logs")
async def get_appium_logs(limit: int = 400):
    return {"logs": await asyncio.to_thread(process_manager.read_logs, limit)}


# --------------------------------------------------------------------------- #
# Devices & sessions
# --------------------------------------------------------------------------- #

@app.get("/api/devices")
async def get_devices():
    return {"devices": await device_discovery.list_devices()}


@app.get("/api/devices/{udid}/apps")
async def get_device_apps(udid: str, platform: str = "Android"):
    if browserstack.is_cloud_device(udid):
        # A cloud device has no installed apps to list; what it can run is
        # whatever the account has uploaded.
        apps = await _browserstack_apps()
    else:
        apps = await device_discovery.list_installed_apps(udid, platform)
    # The TK builds are called out separately so the common choice is one tap
    # rather than a search through every app on the phone.
    return {"apps": apps, "environments": device_discovery.match_environments(apps)}


# --------------------------------------------------------------------------- #
# BrowserStack — the same session, on a device nobody has to keep on a desk
# --------------------------------------------------------------------------- #

class BrowserStackCredentials(BaseModel):
    username: str
    accessKey: str


async def _browserstack_apps() -> List[Dict[str, Any]]:
    if not browserstack.configured():
        return []
    try:
        return [
            {"id": app["id"], "name": app["name"], "version": app.get("version", "")}
            for app in await browserstack.list_apps()
        ]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"BrowserStack: {exc}")


@app.get("/api/browserstack/status")
async def browserstack_status():
    user, _ = browserstack.credentials()
    return {"configured": browserstack.configured(), "username": user or None}


@app.post("/api/browserstack/credentials")
async def save_browserstack_credentials(body: BrowserStackCredentials):
    username, access_key = body.username.strip(), body.accessKey.strip()
    if not username or not access_key:
        raise HTTPException(status_code=400, detail="Both a username and an access key are required.")
    config.write_env({
        "BROWSERSTACK_USERNAME": username,
        "BROWSERSTACK_ACCESS_KEY": access_key,
    })
    try:
        await browserstack.list_devices()
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"BrowserStack did not answer: {exc}")
    return {"status": "success", "username": username}


@app.get("/api/browserstack/devices")
async def browserstack_devices():
    if not browserstack.configured():
        raise HTTPException(
            status_code=400,
            detail="Add your BrowserStack username and access key in Settings first.",
        )
    try:
        return {"devices": await browserstack.list_devices()}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"BrowserStack: {exc}")


@app.get("/api/browserstack/apps")
async def browserstack_apps():
    return {"apps": await _browserstack_apps()}


class SessionRequest(BaseModel):
    udid: str
    platform: str = "Android"
    appId: Optional[str] = None
    name: Optional[str] = None


def _capabilities(req: SessionRequest) -> Dict[str, Any]:
    if req.platform.lower() == "ios":
        always: Dict[str, Any] = {
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:udid": req.udid,
            "appium:deviceName": req.name or "iPhone",
            "appium:noReset": True,
        }
        # A re-signed WebDriverAgent carries a different bundle id, and Appium
        # would otherwise go looking for the stock one and not find it.
        if WDA_BUNDLE_ID:
            always["appium:updatedWDABundleId"] = WDA_BUNDLE_ID
        if req.appId:
            always["appium:bundleId"] = req.appId
    else:
        always = {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:udid": req.udid,
            "appium:noReset": True,
            # Leaving the app running between sessions keeps inspection fast.
            "appium:dontStopAppOnReset": True,
        }
        if req.appId:
            always["appium:appPackage"] = req.appId
            always["appium:appActivity"] = ""
            always["appium:appWaitActivity"] = "*"
    return {"capabilities": {"alwaysMatch": always}}


@app.post("/api/appium/session")
async def create_appium_session(req: SessionRequest):
    # A cloud device is booked from BrowserStack's own hub, so the local Appium
    # server is neither used nor required for one.
    on_cloud = browserstack.is_cloud_device(req.udid)
    if on_cloud and not browserstack.configured():
        raise HTTPException(
            status_code=400,
            detail="Add your BrowserStack username and access key in Settings first.",
        )
    if not on_cloud and not await appium.is_server_running():
        raise HTTPException(status_code=503, detail="Appium server is not running. Start it in Settings.")

    if on_cloud:
        capabilities = browserstack.capabilities(
            udid=req.udid, platform=req.platform, app_id=req.appId, name=req.name,
        )
        hub = browserstack.hub_url()
    else:
        capabilities, hub = _capabilities(req), None

    try:
        response = await appium.create_session(capabilities, base_url=hub)
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if response.status_code != 200:
        detail = response.text
        try:
            detail = response.json().get("value", {}).get("message", detail)
        except Exception:
            pass
        where = "BrowserStack" if on_cloud else "Appium"
        raise HTTPException(status_code=response.status_code, detail=f"{where} refused the session: {detail}")

    payload = response.json()
    session_id = payload.get("value", {}).get("sessionId")
    if not session_id:
        raise HTTPException(status_code=500, detail="Appium returned no sessionId.")

    # Bound before anything else touches the session: every later call has to
    # reach the hub the session was actually made on.
    if hub:
        appium.bind_session(session_id, hub)

    platform_cache[session_id] = req.platform
    device = {
        "udid": req.udid,
        "platform": req.platform,
        "name": req.name or req.udid,
        "appId": req.appId,
        "kind": "mobile",
        "source": "browserstack" if on_cloud else "local",
    }
    drivers.register(MobileTarget(session_id, device, platform_cache))
    return {"status": "success", "sessionId": session_id, "device": device}


# --------------------------------------------------------------------------- #
# Web sessions — the same agent, driving a browser page
# --------------------------------------------------------------------------- #

class WebSessionRequest(BaseModel):
    url: str
    viewport: str = "desktop"   # desktop | tablet | mobile
    browser: str = "chromium"   # chromium | firefox | webkit
    headless: bool = True
    # When the UI knows how big the panel is, the page is rendered at exactly
    # that size so it fills it without letterboxing.
    width: Optional[int] = None
    height: Optional[int] = None


class ViewportRequest(BaseModel):
    width: int
    height: int


# The signature of a connection that opens and then dies without a response:
# either bot protection rejecting a headless browser, or a TLS-inspecting
# antivirus failing to relay. Both look identical to Chromium. The list lives
# with the driver that produces these errors, so the two cannot drift apart.
_HEADLESS_BLOCK_SIGNATURES = web_driver.TRANSIENT_SIGNATURES


def _looks_like_headless_block(exc: Exception) -> bool:
    return web_driver.looks_transient(str(exc)) or "error page" in str(exc)


# Chromium's navigation errors are accurate but unactionable — "protocol error"
# says nothing about what a user would have to change. Each hint therefore says
# what happened in plain words and, where there is one, what to actually do.
_NAVIGATION_HINTS = (
    (
        _HEADLESS_BLOCK_SIGNATURES,
        "Bağlantı kuruldu ama site hiçbir şey göndermedi — ekransız (arka planda "
        "çalışan) tarayıcıyla birkaç kez denendi. Bunun iki yaygın sebebi var: sitenin otomatik "
        "tarayıcıları engelleyen koruması, ya da HTTPS trafiğini tarayan bir antivirüs / "
        "kurumsal vekil sunucu (Avast, AVG, Kaspersky, ESET, Bitdefender hepsinde var) — "
        "sertifikayı kendi imzasıyla değiştirip trafiği aktaramıyor. Site normal "
        "tarayıcınızda açılıyorsa, antivirüsün web tarama ayarlarında bu alan adını "
        "istisnalara ekleyip tekrar deneyin. Bazı siteler aralıklı engeller; birkaç "
        "dakika sonra yeniden denemek de işe yarayabilir.",
    ),
    (
        ("ERR_NAME_NOT_RESOLVED",),
        "Bu alan adı çözümlenemiyor. Yazımını ya da DNS ayarınızı kontrol edin.",
    ),
    (
        ("ERR_CONNECTION_REFUSED",),
        "Bu adreste dinleyen bir şey yok. Yerel bir sunucuysa çalıştığından emin olun.",
    ),
    (
        ("ERR_CONNECTION_TIMED_OUT", "Timeout", "exceeded"),
        "Site zamanında yanıt vermedi. Yavaş olabilir, bu ağda engelli olabilir ya da "
        "otomatik tarayıcıları reddediyor olabilir.",
    ),
    (
        ("ERR_CERT_", "SSL_ERROR"),
        "Sitenin sertifikası reddedildi. Kendi imzalı sertifika kullanan bir iç ortamsa, "
        "sertifikanın önce bu makinede güvenilir olarak tanımlanması gerekir.",
    ),
    (
        ("Executable doesn't exist", "playwright install"),
        "Tarayıcı kurulu değil. Şunu çalıştırın: python -m playwright install chromium",
    ),
)


def _explain_navigation_failure(url: str, exc: Exception) -> str:
    raw = str(exc).split("\n")[0][:220]
    for needles, hint in _NAVIGATION_HINTS:
        if any(needle in str(exc) for needle in needles):
            return f"{url} açılamadı. {hint}\n\nTarayıcı hatası: {raw}"
    return f"{url} açılamadı: {raw}"


@app.post("/api/web/session")
async def create_web_session(req: WebSessionRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="A URL is required.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    note = None
    try:
        # Every site opens the same way: in the background, shown inside QAi.
        # There used to be a fallback here that reopened a refusing site in a
        # visible browser window. It is gone — a second window on the desktop
        # is not what "open this page" should do, and the sites that refuse a
        # background browser serve it a blank shell whether or not the window
        # is visible, so the window bought nothing.
        target = await WebTarget.launch(
            url=url, viewport=req.viewport, headless=req.headless,
            browser_name=req.browser, width=req.width, height=req.height,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_explain_navigation_failure(url, exc))

    drivers.register(target)
    return {
        "status": "success",
        "sessionId": target.session_id,
        "note": note,
        "device": {
            "udid": target.session_id,
            "platform": "Web",
            "name": target.page.url or url,
            "appId": url,
            "kind": "web",
            "viewport": req.viewport,
            "browser": req.browser,
            "headless": target.config.get("headless", True),
            "title": await target.page.title(),
        },
    }


class NavigateRequest(BaseModel):
    url: str


@app.post("/api/web/session/{session_id}/navigate")
async def navigate_web_session(session_id: str, req: NavigateRequest):
    target = require_target(session_id)
    if target.kind != "web":
        raise HTTPException(status_code=400, detail="That session is a device, not a browser page.")

    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        # Not a bare goto(): a refused connection leaves Chromium's error page
        # behind without raising, and reporting that as a success is how a
        # session ends up sitting on "Bu siteye ulaşılamıyor".
        result = await target.navigate(url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_explain_navigation_failure(url, exc))
    return {"status": "success", **result}


@app.post("/api/web/session/{session_id}/reopen")
async def reopen_web_session(session_id: str):
    """Recover a page that crashed or was closed, without losing the session."""
    target = require_target(session_id)
    if target.kind != "web":
        raise HTTPException(status_code=400, detail="That session is a device, not a browser page.")
    try:
        info = await target.reopen()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_explain_navigation_failure(
            target.config.get("url", "the page"), exc))
    return {"status": "success", **info}


@app.post("/api/web/session/{session_id}/viewport")
async def set_web_viewport(session_id: str, req: ViewportRequest):
    """Re-render the page at the size of the panel showing it."""
    target = require_target(session_id)
    if target.kind != "web":
        raise HTTPException(status_code=400, detail="That session is a device, not a browser page.")
    try:
        size = await target.set_viewport(req.width, req.height)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not resize: {str(exc).splitlines()[0][:200]}")
    return {"status": "success", "screen": size}


@app.get("/api/sessions")
@app.get("/api/appium/sessions")
async def list_sessions():
    out = []
    for session_id, target in drivers.all_targets().items():
        info = target.describe()
        out.append({
            "sessionId": session_id,
            "device": {
                "udid": info.get("udid"),
                "platform": info.get("platform"),
                "name": info.get("name"),
                "appId": info.get("appId"),
                "kind": target.kind,
            },
        })
    return {"sessions": out}


@app.delete("/api/session/{session_id}")
@app.delete("/api/appium/session/{session_id}")
async def delete_session(session_id: str):
    agent.cancel(session_id)
    agent.reset(session_id)
    platform_cache.pop(session_id, None)
    await drivers.close(session_id)
    return {"status": "success"}


# --------------------------------------------------------------------------- #
# Screen & DOM
# --------------------------------------------------------------------------- #

@app.get("/api/session/{session_id}/screenshot")
@app.get("/api/appium/session/{session_id}/screenshot")
async def get_session_screenshot(session_id: str):
    target = require_target(session_id)
    # Same JPEG frame the socket uses, so the polling fallback is as cheap.
    if target.kind == "web":
        frame = await target.mirror_frame()
        if frame is None:
            raise HTTPException(status_code=502, detail="Could not read a screenshot from the target.")
        return {"screenshot": frame}
    screenshot = await target.screenshot()
    if screenshot is None:
        raise HTTPException(status_code=502, detail="Could not read a screenshot from the target.")
    return {"screenshot": await asyncio.to_thread(agent._shrink_for_mirror, screenshot)}


# A real device's screenshot pipeline is not free — on a physical iPhone it
# routinely costs 200-300ms per frame — so a "live" mirror is really a fast
# poll, not video. The mobile UI never asks for more than 4; the web
# workspace's own slider goes up to 15 for a Playwright page, which is cheap
# enough to actually sustain that. This ceiling only guards against a runaway
# client asking for more than either one would.
_MIRROR_MAX_FPS = 15.0
_MIRROR_DEFAULT_FPS = 2.0


@app.websocket("/ws/session/{session_id}/screen")
async def stream_screen(websocket: WebSocket, session_id: str):
    """Push screenshots over a socket instead of polling HTTP for every frame.

    The client sends {"fps": n} to retune; frames are only sent when the image
    actually changed, so a static screen costs nothing.

    While an agent run is driving this session, this loop stops calling
    target.screenshot() on its own timer — the run already grabs a frame right
    before and right after every action, and polling on top of that only makes
    both slower for a picture that would not have changed in between anyway.
    It instead reads whatever the run last published.
    """
    await websocket.accept()
    interval = 1 / _MIRROR_DEFAULT_FPS
    last_digest = None

    async def read_control():
        nonlocal interval
        try:
            while True:
                message = await websocket.receive_text()
                try:
                    fps = float(json.loads(message).get("fps", _MIRROR_DEFAULT_FPS))
                    interval = 1 / max(1.0, min(fps, _MIRROR_MAX_FPS))
                except Exception:
                    pass
        except Exception:
            pass

    control_task = asyncio.create_task(read_control())
    try:
        while True:
            target = drivers.get(session_id)
            if target is None:
                await websocket.send_text(json.dumps({"dead": True, "reason": "session closed"}))
                break

            # A crashed page answers "no frame" forever, which looks exactly
            # like a slow screen. Say it is dead so the UI can offer a way out
            # instead of freezing on the last frame it ever received.
            if not target.is_alive():
                await websocket.send_text(json.dumps({"dead": True, "reason": "the page was closed or crashed"}))
                await asyncio.sleep(interval)
                continue

            if agent.is_running(session_id):
                screenshot = agent.get_live_frame(session_id)
                if screenshot:
                    digest = hash(screenshot)
                    if digest != last_digest:
                        last_digest = digest
                        await websocket.send_text(json.dumps({"screenshot": screenshot}))
                    else:
                        await websocket.send_text(json.dumps({"unchanged": True}))
                await asyncio.sleep(max(interval, 0.5))
                continue

            # A web page captures a JPEG straight from Chromium at display size,
            # which needs no re-encoding and keeps the mirror responsive. A real
            # device hands back a large PNG, so that path still shrinks off-thread.
            if target.kind == "web":
                screenshot = await target.mirror_frame()
            else:
                screenshot = await asyncio.to_thread(agent._shrink_for_mirror, await target.screenshot())
            if screenshot:
                digest = hash(screenshot)
                if digest != last_digest:
                    last_digest = digest
                    await websocket.send_text(json.dumps({"screenshot": screenshot}))
                else:
                    await websocket.send_text(json.dumps({"unchanged": True}))
            else:
                await websocket.send_text(json.dumps({"error": "no frame"}))
            await asyncio.sleep(interval)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:
        print(f"[ws] screen stream ended: {exc}")
    finally:
        control_task.cancel()


@app.get("/api/session/{session_id}/source")
@app.get("/api/appium/session/{session_id}/source")
async def get_session_source(session_id: str):
    target = require_target(session_id)
    snapshot = await target.snapshot()
    if snapshot is None:
        raise HTTPException(status_code=502, detail="Could not read the current screen.")

    payload = {
        "source": snapshot.get_optimized_tree(),
        "snapshotId": snapshot.snapshot_id,
        "screen": {"width": snapshot.screen_width, "height": snapshot.screen_height},
        "kind": target.kind,
        "elementCount": len(snapshot.get_all_elements()),
    }
    if target.kind == "web":
        payload["page"] = {"url": snapshot.url, "title": snapshot.title, "scrollY": snapshot.scroll_y}
    return payload


@app.get("/api/session/{session_id}/element-at")
@app.get("/api/appium/session/{session_id}/element-at")
async def element_at_point(session_id: str, x: int, y: int):
    """Smallest element whose bounds contain (x, y) — powers click-to-inspect."""
    target = require_target(session_id)
    element = await target.element_at(x, y)
    return {"element": element}


# --------------------------------------------------------------------------- #
# Manual control
# --------------------------------------------------------------------------- #

class GestureRequest(BaseModel):
    type: str
    x: Optional[int] = None
    y: Optional[int] = None
    endX: Optional[int] = None
    endY: Optional[int] = None
    duration: Optional[int] = 300
    key: Optional[str] = None
    text: Optional[str] = None
    direction: Optional[str] = None
    dx: Optional[float] = None
    dy: Optional[float] = None


@app.post("/api/session/{session_id}/gesture")
@app.post("/api/appium/session/{session_id}/gesture")
async def perform_gesture_action(session_id: str, req: GestureRequest):
    target = require_target(session_id)
    kind = req.type

    if target.kind == "web":
        return await _web_gesture(target, req)

    controller = MobileGestureController

    def need_xy():
        if req.x is None or req.y is None:
            raise HTTPException(status_code=400, detail=f"x and y are required for {kind}")

    if kind == "tap":
        need_xy()
        ok = await controller.perform_tap(session_id, req.x, req.y)
    elif kind == "double_tap":
        need_xy()
        ok = await controller.perform_double_tap(session_id, req.x, req.y)
    elif kind == "long_press":
        need_xy()
        ok = await controller.perform_long_press(session_id, req.x, req.y, req.duration or 1000)
    elif kind == "swipe":
        need_xy()
        if req.endX is None or req.endY is None:
            raise HTTPException(status_code=400, detail="endX and endY are required for swipe")
        ok = await controller.perform_swipe(session_id, req.x, req.y, req.endX, req.endY, req.duration or 500)
    elif kind == "scroll":
        size = await appium.get_window_size(session_id)
        ok = await controller.perform_scroll(session_id, req.direction or "down", size["width"], size["height"])
    elif kind == "key":
        if not req.key:
            raise HTTPException(status_code=400, detail="key is required for a key action")
        platform = await appium.get_platform(session_id, platform_cache)
        ok = await controller.perform_key_event(session_id, platform, req.key)
    elif kind == "type_text":
        if req.text is None:
            raise HTTPException(status_code=400, detail="text is required for type_text")
        ok = await controller.perform_type_text(session_id, req.text)
    elif kind == "hide_keyboard":
        ok = await controller.hide_keyboard(session_id)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported gesture type: {kind}")

    if not ok:
        raise HTTPException(status_code=502, detail=f"The device did not accept the '{kind}' gesture.")
    return {"status": "success", "message": f"Executed {kind}."}


async def _web_gesture(target, req: GestureRequest):
    """Manual mouse/keyboard control of a browser page, from the mirror panel."""
    kind = req.type
    page = target.page
    try:
        if kind in ("tap", "double_tap", "long_press"):
            if req.x is None or req.y is None:
                raise HTTPException(status_code=400, detail=f"x and y are required for {kind}")
            if kind == "double_tap":
                await page.mouse.dblclick(req.x, req.y)
            else:
                await page.mouse.click(req.x, req.y, delay=req.duration if kind == "long_press" else 0)
        elif kind in ("pointer_down", "pointer_move", "pointer_up"):
            # Press and release are separate so the panel can forward what the
            # user's mouse is actually doing, as they do it, instead of only
            # ever delivering a finished click.
            if kind == "pointer_up":
                result = await target.pointer_up(req.x, req.y)
            else:
                if req.x is None or req.y is None:
                    raise HTTPException(status_code=400, detail=f"x and y are required for {kind}")
                result = await (
                    target.pointer_down(req.x, req.y) if kind == "pointer_down"
                    else target.pointer_move(req.x, req.y)
                )
            if not result.ok:
                raise HTTPException(status_code=502, detail=result.message)
        elif kind == "swipe":
            if None in (req.x, req.y, req.endX, req.endY):
                raise HTTPException(status_code=400, detail="x, y, endX and endY are required for swipe")
            await page.mouse.move(req.x, req.y)
            await page.mouse.down()
            await page.mouse.move(req.endX, req.endY, steps=12)
            await page.mouse.up()
        elif kind == "scroll":
            result = await target.scroll(req.direction or "down")
            if not result.ok:
                raise HTTPException(status_code=502, detail=result.message)
        elif kind == "wheel":
            # The user's own wheel, forwarded as raw pixel deltas so a small
            # nudge scrolls a little and a big spin scrolls a lot — unlike the
            # agent's "scroll" which always moves most of a screen. No settle
            # sleep and no snapshot: this fires many times a second by hand.
            await page.mouse.wheel(req.dx or 0, req.dy or 0)
        elif kind == "key":
            if not req.key:
                raise HTTPException(status_code=400, detail="key is required for a key action")
            result = await target.press_key(req.key)
            if not result.ok:
                raise HTTPException(status_code=400, detail=result.message)
        elif kind == "type_text":
            if req.text is None:
                raise HTTPException(status_code=400, detail="text is required for type_text")
            await page.keyboard.type(req.text)
        elif kind == "hide_keyboard":
            return {"status": "success", "message": "No on-screen keyboard on the web target."}
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported gesture type: {kind}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{kind} failed: {str(exc).splitlines()[0][:220]}")

    return {"status": "success", "message": f"Executed {kind}."}


class ActionRequest(BaseModel):
    action: str
    xpath: Optional[str] = None
    elementId: Optional[str] = None
    snapshotId: Optional[str] = None
    value: Optional[str] = ""


@app.post("/api/session/{session_id}/action")
@app.post("/api/appium/session/{session_id}/action")
async def perform_action(session_id: str, req: ActionRequest):
    """Single element action, used by the inspector's manual controls."""
    if not req.xpath and not req.elementId:
        raise HTTPException(status_code=400, detail="Provide either xpath or elementId.")

    target = require_target(session_id)
    result = await target.act(req.action, req.elementId, req.xpath, req.value, req.snapshotId)
    if not result.ok:
        raise HTTPException(status_code=422, detail=result.message)
    return {"status": "success", "message": result.message, "element": result.element}


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #

class ScenarioStep(BaseModel):
    """One written step: what to do, and what it should produce.

    `expected` is what makes the step checkable rather than merely performed —
    a run reports each step against it, so a step without one can only ever be
    reported as "carried out".
    """
    action: str
    expected: Optional[str] = None


class AgentRunRequest(BaseModel):
    goal: str
    # A scenario written as steps is run and judged one step at a time. Sent
    # from the workspace so a saved scenario can be tried against the connected
    # browser or device without going through a whole Test Set run.
    steps: Optional[List[ScenarioStep]] = None
    maxSteps: Optional[int] = None
    useVision: bool = True
    tags: List[str] = []
    failOnPageError: bool = True
    # Both override Settings for this run only, so two models can be compared
    # on one scenario without editing a global default between attempts.
    model: Optional[str] = None
    effort: Optional[str] = None


@app.post("/api/session/{session_id}/agent/run")
@app.post("/api/appium/session/{session_id}/agent/run")
async def agent_run(session_id: str, req: AgentRunRequest):
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="A test goal is required.")

    target = require_target(session_id)
    stream = agent.run_agent(
        target=target,
        goal=req.goal.strip(),
        steps=[step.model_dump() for step in req.steps] if req.steps else None,
        max_steps=req.maxSteps,
        use_vision=req.useVision,
        model=req.model,
        effort=req.effort,
    )
    return StreamingResponse(
        _with_page_events(stream, target, req.tags, req.failOnPageError),
        media_type="application/x-ndjson",
    )


async def _with_page_events(stream, target, tags: List[str], fail_on_page_error: bool):
    """Fold the page's own errors into the run as it closes.

    The agent loop is target-agnostic and knows nothing about consoles or HTTP
    status codes, so the web-specific verdict is applied here, where the run id
    is finally known.
    """
    run_id = None
    status = None

    async for line in stream:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            yield line
            continue

        if payload.get("event") in ("run_started", "run_closed") and payload.get("runId"):
            run_id = payload["runId"]
        if payload.get("event") == "finished":
            status = payload.get("status")

        # Hold the closing event back until the verdict has been revised.
        if payload.get("event") == "run_closed" and run_id:
            events = _drain(target)
            storage.add_page_events(run_id, events)
            if tags:
                storage.set_run_tags(run_id, tags)

            errors = [e for e in events if e.get("level") == "error"]
            if errors and status == "passed" and fail_on_page_error:
                note = (
                    f"{len(errors)} page error(s) while the run was otherwise green — "
                    f"first: {(errors[0].get('text') or '')[:160]}"
                )
                storage.finish_run(run_id, "failed", note, verdict_note=note)
                payload["status"] = "failed"
                payload["pageErrors"] = len(errors)
                yield json.dumps(
                    {"event": "page_errors", "count": len(errors), "message": note},
                    ensure_ascii=False,
                ) + "\n"
            elif errors:
                yield json.dumps(
                    {"event": "page_errors", "count": len(errors),
                     "message": f"{len(errors)} page error(s) recorded."},
                    ensure_ascii=False,
                ) + "\n"

            yield json.dumps(payload, ensure_ascii=False) + "\n"
            continue

        yield line


def _drain(target) -> List[Dict[str, Any]]:
    if not hasattr(target, "drain_events"):
        return []
    try:
        return target.drain_events()
    except Exception:
        return []


@app.post("/api/session/{session_id}/agent/stop")
@app.post("/api/appium/session/{session_id}/agent/stop")
async def agent_stop(session_id: str):
    stopped = agent.cancel(session_id)
    return {"status": "success", "stopped": stopped}


@app.get("/api/session/{session_id}/agent/status")
@app.get("/api/appium/session/{session_id}/agent/status")
async def agent_status(session_id: str):
    state = agent.get_session(session_id)
    return {"running": state.running, "runId": state.run_id}


# --------------------------------------------------------------------------- #
# Runs, reports, export
# --------------------------------------------------------------------------- #

@app.get("/api/runs")
async def get_runs(
    limit: int = 50,
    priority: Optional[str] = None,
    q: Optional[str] = None,
    offset: int = 0,
):
    runs = storage.list_runs(limit, priority=priority, search=q, offset=offset)
    # Whether another page exists, so the UI can hide "Load more" at the end
    # rather than offering a button that returns nothing.
    return {"runs": runs, "hasMore": len(runs) == limit}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    # The UI offers only the formats this run's target can actually produce.
    run["exportFormats"] = exporters.formats_for(run)
    return run


@app.get("/api/runs/{run_id}/steps/{step_id}/screenshot")
async def get_step_screenshot(run_id: str, step_id: int):
    screenshot = storage.get_step_screenshot(run_id, step_id)
    if screenshot is None:
        raise HTTPException(status_code=404, detail="No screenshot recorded for that step.")
    return {"screenshot": screenshot}


class RenameRequest(BaseModel):
    title: str


@app.patch("/api/runs/{run_id}")
async def rename_run(run_id: str, req: RenameRequest):
    if not storage.rename_run(run_id, req.title):
        raise HTTPException(status_code=404, detail="Run not found.")
    return {"status": "success"}


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    if not storage.delete_run(run_id):
        raise HTTPException(status_code=404, detail="Run not found.")
    return {"status": "success"}


@app.get("/api/runs/{run_id}/export")
async def export_run(run_id: str, format: str = "pytest"):
    run = storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    try:
        return exporters.export(run, format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/runs/{run_id}/replay")
async def replay_run(run_id: str, session_id: str, heal: bool = True):
    """Re-execute a recorded run deterministically, repairing what has drifted.

    Replay costs nothing and is perfectly repeatable — until the page changes
    underneath it. `heal` lets each step fall back to a semantic re-match and,
    if that is not enough, to the model, so a suite survives a redesign instead
    of needing to be re-recorded.
    """
    run = storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    target = require_target(session_id)
    replay_id = storage.create_run(
        goal=f"Replay of {run['title']}",
        platform=run.get("platform"),
        device_name=run.get("device_name"),
        device_udid=run.get("device_udid"),
        app_id=run.get("app_id"),
        model="replay",
        kind=run.get("kind"),
        tags=(run.get("tags") or []) + ["replay"],
        case_id=run.get("case_id"),
    )

    # Resolved once, up front: a missing provider should degrade healing to the
    # free semantic pass, not fail the replay on its first broken step.
    llm = None
    if heal:
        try:
            provider, model, api_key = agent._resolve_provider()
            llm = {"provider": provider, "model": model, "api_key": api_key}
        except Exception as exc:
            print(f"[replay] healing limited to semantic matching: {exc}")

    async def stream():
        yield json.dumps({
            "event": "run_started", "runId": replay_id,
            "goal": f"Replay of {run['title']}", "healing": bool(llm),
        }, ensure_ascii=False) + "\n"

        stats = healing.HealStats()
        status = "passed"
        steps = [s for s in run["steps"] if s["status"] == "passed" and s["action"] != "done"]

        for index, step in enumerate(steps, start=1):
            snapshot = await target.snapshot()

            resolution = healing.HealResult(
                ok=True, strategy="recorded", message="",
                selector=(step.get("element") or {}).get("xpath"),
            )
            # Only element-bound actions can drift; a scroll or a wait has
            # nothing to re-match, so they skip the whole resolution pass.
            if step["action"] in ("click", "type", "clear", "assert_visible"):
                resolution = await healing.resolve_step(target, snapshot, step, llm)
                stats.record(index, resolution)
                if resolution.healed:
                    yield json.dumps({
                        "event": "step_healed", "step": index,
                        "strategy": resolution.strategy, "confidence": resolution.confidence,
                        "message": resolution.message,
                    }, ensure_ascii=False) + "\n"

            if not resolution.ok:
                step_id = storage.add_step(
                    replay_id, action=step["action"], status="failed",
                    target=step.get("target"), value=step.get("value"),
                    reason="replay", message=resolution.message,
                    screenshot=await target.screenshot(),
                )
                yield json.dumps({
                    "event": "step_finished", "step": index, "stepId": step_id,
                    "action": step["action"], "status": "failed", "message": resolution.message,
                }, ensure_ascii=False) + "\n"
                status = "failed"
                break

            result = await agent._execute_action(
                target,
                {
                    "action": step["action"],
                    "elementId": resolution.element_id,
                    "xpath": resolution.selector,
                    "value": step.get("value"),
                },
                snapshot,
            )
            shot = await target.screenshot()
            step_status = "passed" if result["ok"] else "failed"
            message = result["message"]
            if resolution.healed and result["ok"]:
                message = f"{message} — {resolution.message}"

            step_id = storage.add_step(
                replay_id, action=step["action"], status=step_status,
                target=step.get("target"), value=step.get("value"),
                reason="replay", message=message,
                element=result.get("element"), screenshot=shot,
                selector=resolution.selector, healed=resolution.healed,
            )
            yield json.dumps({
                "event": "step_finished", "step": index, "stepId": step_id,
                "action": step["action"], "status": step_status, "message": message,
                "healed": resolution.healed,
            }, ensure_ascii=False) + "\n"

            if not result["ok"]:
                status = "failed"
                break
            await asyncio.sleep(0.5)

        _capture_page_events(target, replay_id)

        note = None
        if stats.healed:
            note = (
                f"{stats.healed} step(s) were repaired against the current page. "
                "Re-export the script to pick up the new selectors."
            )
        storage.finish_run(replay_id, status, verdict_note=note)
        yield json.dumps({
            "event": "finished", "status": status,
            "healed": stats.healed, "healDetails": stats.details,
        }, ensure_ascii=False) + "\n"
        yield json.dumps({"event": "run_closed", "runId": replay_id, "status": status}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


def _capture_page_events(target, run_id: str) -> int:
    """Move whatever the page complained about into the run's record."""
    return storage.add_page_events(run_id, _drain(target))


# --------------------------------------------------------------------------- #
# Suites — the unit CI actually runs
# --------------------------------------------------------------------------- #

class SuiteBody(BaseModel):
    name: str
    description: Optional[str] = None
    kind: str = "web"
    tags: List[str] = []


class CaseBody(BaseModel):
    name: str
    goal: str
    url: Optional[str] = None
    tags: List[str] = []
    dataset: Optional[List[Dict[str, Any]]] = None
    authProfile: Optional[str] = None
    sourceRunId: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[str] = None
    layer: Optional[str] = None
    steps: Optional[List[ScenarioStep]] = None


class ScenarioGenerateBody(BaseModel):
    brief: Optional[str] = None
    kind: str = "web"
    url: Optional[str] = None
    useVision: bool = True
    model: Optional[str] = None
    effort: str = "medium"
    # Answers to questions the model asked on a previous, too-vague attempt.
    answers: Optional[str] = None


class BulkCaseBody(BaseModel):
    """Generated scenarios are reviewed as a batch, so they are saved as one."""
    cases: List[CaseBody]


class SuiteRunBody(BaseModel):
    workers: int = 1
    tags: Optional[List[str]] = None
    headless: bool = True
    baseUrl: Optional[str] = None
    authProfile: Optional[str] = None
    trace: bool = False
    recordVideo: bool = False
    width: int = 1440
    height: int = 900
    failOnPageError: bool = True


@app.get("/api/suites")
async def get_suites():
    return {"suites": storage.list_suites()}


@app.post("/api/suites")
async def post_suite(body: SuiteBody):
    suite_id = storage.create_suite(body.name, body.description, body.kind, body.tags)
    return storage.get_suite(suite_id)


@app.get("/api/suites/{suite_id}")
async def get_suite(suite_id: str):
    suite = storage.get_suite(suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found.")
    return suite


@app.patch("/api/suites/{suite_id}")
async def patch_suite(suite_id: str, body: SuiteBody):
    if not storage.update_suite(
        suite_id, name=body.name, description=body.description,
        kind=body.kind, tags=body.tags,
    ):
        raise HTTPException(status_code=404, detail="Suite not found.")
    return storage.get_suite(suite_id)


@app.delete("/api/suites/{suite_id}")
async def remove_suite(suite_id: str):
    if not storage.delete_suite(suite_id):
        raise HTTPException(status_code=404, detail="Suite not found.")
    return {"deleted": True}


@app.post("/api/suites/{suite_id}/cases")
async def post_case(suite_id: str, body: CaseBody):
    if storage.get_suite(suite_id) is None:
        raise HTTPException(status_code=404, detail="Suite not found.")
    case_id = storage.add_case(
        suite_id, body.name, body.goal, url=body.url, tags=body.tags,
        dataset=body.dataset, auth_profile=body.authProfile,
        source_run_id=body.sourceRunId, priority=body.priority, layer=body.layer,
        steps=[step.model_dump() for step in body.steps] if body.steps else None,
    )
    return storage.get_case(case_id)


@app.post("/api/suites/{suite_id}/cases/bulk")
async def post_cases_bulk(suite_id: str, body: BulkCaseBody):
    if storage.get_suite(suite_id) is None:
        raise HTTPException(status_code=404, detail="Suite not found.")
    added = [
        storage.get_case(storage.add_case(
            suite_id, case.name, case.goal, url=case.url, tags=case.tags,
            dataset=case.dataset, auth_profile=case.authProfile,
            source_run_id=case.sourceRunId, priority=case.priority, layer=case.layer,
            steps=[step.model_dump() for step in case.steps] if case.steps else None,
        ))
        for case in body.cases
    ]
    return {"added": len(added), "cases": added}


class ExecutionBody(BaseModel):
    """An execution assembled by hand.

    `caseIds` may come from any number of Test Sets — picking a few scenarios
    out of one set, or combining several sets, is the same operation, so there
    is one endpoint for both rather than a special case for each.
    """
    caseIds: List[str]
    name: Optional[str] = None
    workers: int = 2
    headless: bool = True
    trace: bool = False
    recordVideo: bool = False
    failOnPageError: bool = True


@app.post("/api/executions")
async def post_execution(body: ExecutionBody):
    cases = storage.cases_by_id(body.caseIds)
    if not cases:
        raise HTTPException(
            status_code=400,
            detail="None of those scenarios exist any more. Reload the Test Set and pick again.",
        )

    sources = list({
        (case.get("suite_id"), case.get("suite_name")) for case in cases
    })
    stream = suite_runner.run_suite(
        suite_id=cases[0].get("suite_id") if len(sources) == 1 else None,
        workers=body.workers,
        cases=cases,
        name=body.name,
        sources=[{"suite_id": sid, "suite_name": sname} for sid, sname in sources],
        headless=body.headless,
        trace=body.trace,
        record_video=body.recordVideo,
        fail_on_page_error=body.failOnPageError,
    )
    return StreamingResponse(stream, media_type="application/x-ndjson")


@app.post("/api/scenarios/generate")
async def generate_scenarios(body: ScenarioGenerateBody):
    """Scenarios from a written brief, and from the page itself when given one.

    A brief naming a page ("the Turkish Airlines homepage") is worth far more
    when the page is actually read: scenarios then name the real fields and
    buttons instead of what the model imagines the site contains. So a URL is
    opened headlessly here, read once, and closed — the caller does not have to
    keep a session open to get that.
    """
    tree = screenshot = None
    url = (body.url or "").strip()
    if url:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            # In the background, like the workspace: reading a page to write
            # scenarios from should never put a browser window on the desktop.
            target = await WebTarget.launch(url=url, viewport="desktop", headless=True)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_explain_navigation_failure(url, exc))
        try:
            snapshot = await target.snapshot()
            if snapshot is not None:
                tree = snapshot.get_optimized_tree_for_llm()
                if body.useVision:
                    screenshot = agent._shrink_for_llm(await target.screenshot())
        finally:
            # Read once and closed: this is a one-shot look, not a session the
            # caller has to remember to clean up.
            await target.close()

    try:
        return await scenario_writer.generate(
            kind=body.kind, brief=body.brief, tree=tree, url=body.url,
            screenshot=screenshot, answers=body.answers,
            model=body.model, effort=body.effort,
        )
    except (RuntimeError, ProviderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/session/{session_id}/scenarios/generate")
@app.post("/api/appium/session/{session_id}/scenarios/generate")
async def generate_scenarios_from_screen(session_id: str, body: ScenarioGenerateBody):
    """Scenarios from what is actually on screen.

    Written against the live element tree, so they name real fields and real
    buttons rather than what the model imagines the page contains.
    """
    target = require_target(session_id)
    snapshot = await target.snapshot()
    if snapshot is None:
        raise HTTPException(status_code=502, detail="Could not read the current screen.")

    screenshot = await target.screenshot() if body.useVision else None
    info = target.describe()
    try:
        return await scenario_writer.generate(
            kind=target.kind, brief=body.brief,
            tree=snapshot.get_optimized_tree_for_llm(),
            url=body.url or info.get("appId") or info.get("name"),
            screenshot=agent._shrink_for_llm(screenshot),
            answers=body.answers, model=body.model, effort=body.effort,
        )
    except (RuntimeError, ProviderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/cases/{case_id}")
async def patch_case(case_id: str, body: CaseBody):
    fields: Dict[str, Any] = {
        "name": body.name, "goal": body.goal, "url": body.url,
        "tags": body.tags, "auth_profile": body.authProfile,
        "priority": body.priority, "layer": body.layer,
    }
    if body.dataset is not None:
        fields["dataset"] = body.dataset
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    if body.steps is not None:
        fields["steps"] = [step.model_dump() for step in body.steps]
    if not storage.update_case(case_id, **fields):
        raise HTTPException(status_code=404, detail="Case not found.")
    return storage.get_case(case_id)


@app.delete("/api/cases/{case_id}")
async def remove_case(case_id: str):
    if not storage.delete_case(case_id):
        raise HTTPException(status_code=404, detail="Case not found.")
    return {"deleted": True}


@app.post("/api/runs/{run_id}/save-as-case")
async def save_run_as_case(run_id: str, suite_id: str, name: Optional[str] = None):
    """Promote a one-off run into a repeatable suite case.

    This is the path from "I tried something in the workspace" to "this is part
    of the regression suite", and it should take one click rather than retyping
    the goal.
    """
    run = storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if storage.get_suite(suite_id) is None:
        raise HTTPException(status_code=404, detail="Suite not found.")

    case_id = storage.add_case(
        suite_id,
        name or run.get("title") or "Untitled case",
        run["goal"],
        url=run.get("app_id"),
        tags=[t for t in (run.get("tags") or []) if t != "replay"],
        source_run_id=run_id,
    )
    return storage.get_case(case_id)


@app.post("/api/suites/{suite_id}/run")
async def post_suite_run(suite_id: str, body: SuiteRunBody):
    """Run a suite, streaming per-case progress as NDJSON."""
    if storage.get_suite(suite_id) is None:
        raise HTTPException(status_code=404, detail="Suite not found.")

    stream = suite_runner.run_suite(
        suite_id,
        workers=body.workers,
        tags=body.tags,
        headless=body.headless,
        base_url=body.baseUrl,
        auth_profile=body.authProfile,
        trace=body.trace,
        record_video=body.recordVideo,
        width=body.width,
        height=body.height,
        fail_on_page_error=body.failOnPageError,
    )
    return StreamingResponse(stream, media_type="application/x-ndjson")


@app.get("/api/suite-runs")
async def get_suite_runs(suite_id: Optional[str] = None, limit: int = 50):
    return {"suiteRuns": storage.list_suite_runs(suite_id, limit)}


@app.get("/api/suite-runs/{suite_run_id}")
async def get_suite_run(suite_run_id: str):
    suite_run = storage.get_suite_run(suite_run_id)
    if suite_run is None:
        raise HTTPException(status_code=404, detail="Suite run not found.")
    return suite_run


@app.get("/api/suite-runs/{suite_run_id}/report")
async def get_suite_report(suite_run_id: str, format: str = "junit"):
    """The CI-shaped report for a finished suite run."""
    suite_run = reporters.report_for_suite_run(suite_run_id)
    if suite_run is None:
        raise HTTPException(status_code=404, detail="Suite run not found.")

    name = suite_run.get("suite_name") or "QAi"
    if format == "junit":
        return Response(
            content=reporters.junit_xml(suite_run["runs"], name),
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="qai-{suite_run_id}.xml"'},
        )
    if format == "json":
        return reporters.json_report(suite_run["runs"], name)
    raise HTTPException(status_code=400, detail="format must be 'junit' or 'json'.")


@app.get("/api/runs/{run_id}/report")
async def get_run_report(run_id: str, format: str = "junit"):
    run = storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if format == "junit":
        return Response(
            content=reporters.junit_xml([run], run.get("title") or "QAi"),
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="qai-{run_id}.xml"'},
        )
    if format == "json":
        return reporters.json_report([run], run.get("title") or "QAi")
    raise HTTPException(status_code=400, detail="format must be 'junit' or 'json'.")


# --------------------------------------------------------------------------- #
# Artifacts, trends
# --------------------------------------------------------------------------- #

@app.get("/api/runs/{run_id}/artifacts")
async def get_run_artifacts(run_id: str):
    return {"artifacts": storage.list_artifacts(run_id)}


@app.get("/api/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: int):
    artifact = storage.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")

    full = os.path.join(run_artifact_dir(artifact["run_id"]), artifact["path"])
    # The path came out of the database, but it is still joined onto a
    # filesystem root — confirm it did not escape the run's own directory.
    root = os.path.realpath(run_artifact_dir(artifact["run_id"]))
    if not os.path.realpath(full).startswith(root) or not os.path.exists(full):
        raise HTTPException(status_code=404, detail="The artifact file is no longer on disk.")

    return FileResponse(
        full,
        filename=f"{artifact['kind']}-{artifact['run_id']}{os.path.splitext(full)[1]}",
        media_type="application/octet-stream",
    )


@app.get("/api/insights/trend")
async def get_trend(days: int = 14):
    return {"trend": storage.trend(days)}


@app.get("/api/insights/priority")
async def get_priority_breakdown(days: int = 14):
    """How the history looks through the priority standard, not just in total."""
    return {"breakdown": storage.priority_breakdown(days)}


@app.get("/api/insights/flaky")
async def get_flaky(limit: int = 20, window: int = 20):
    return {"flaky": storage.flakiness_report(limit, window)}


# --------------------------------------------------------------------------- #
# Saved sign-ins, network mocking, visual baselines
# --------------------------------------------------------------------------- #

class RoutesBody(BaseModel):
    rules: List[Dict[str, Any]] = []


class BaselineBody(BaseModel):
    name: str
    threshold: int = visual.DEFAULT_THRESHOLD
    tolerance: float = visual.DEFAULT_TOLERANCE
    update: bool = False
    width: Optional[int] = None
    height: Optional[int] = None


@app.get("/api/auth-profiles")
async def get_auth_profiles():
    return {"profiles": list_auth_profiles()}


@app.post("/api/session/{session_id}/save-auth")
async def post_save_auth(session_id: str, name: str):
    """Freeze the current sign-in so later runs can start already logged in."""
    target = require_target(session_id)
    if not hasattr(target, "save_auth"):
        raise HTTPException(status_code=400, detail="Only web sessions have a sign-in to save.")
    try:
        path = await target.save_auth(name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save the sign-in: {exc}")
    return {"saved": True, "name": name, "sizeBytes": os.path.getsize(path)}


@app.delete("/api/auth-profiles/{name}")
async def remove_auth_profile(name: str):
    if not delete_auth_profile(name):
        raise HTTPException(status_code=404, detail="No such saved sign-in.")
    return {"deleted": True}


@app.post("/api/session/{session_id}/routes")
async def post_routes(session_id: str, body: RoutesBody):
    """Fulfil or fail matching requests, so error paths can be tested.

    Without this only the happy path is reachable: a live backend that insists
    on working cannot be made to return a 500 on demand.
    """
    target = require_target(session_id)
    if not hasattr(target, "set_routes"):
        raise HTTPException(status_code=400, detail="Only web sessions can mock the network.")
    try:
        count = await target.set_routes(body.rules)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not install the rules: {exc}")
    return {"installed": count, "rules": target.describe_routes()}


@app.get("/api/session/{session_id}/routes")
async def get_routes(session_id: str):
    target = require_target(session_id)
    return {"rules": target.describe_routes() if hasattr(target, "describe_routes") else []}


@app.get("/api/session/{session_id}/page-events")
async def get_session_page_events(session_id: str):
    """What the page has complained about so far, without clearing it."""
    target = require_target(session_id)
    if not hasattr(target, "peek_events"):
        return {"events": []}
    return {"events": target.peek_events()}


@app.get("/api/baselines")
async def get_baselines():
    return {"baselines": visual.list_baselines()}


@app.delete("/api/baselines/{name}")
async def remove_baseline(name: str):
    if not visual.delete_baseline(name):
        raise HTTPException(status_code=404, detail="No such baseline.")
    return {"deleted": True}


@app.get("/api/baselines/{name}/image")
async def get_baseline_image(name: str, diff: bool = False):
    try:
        path = visual.baseline_path(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if diff:
        path = path[:-4] + ".diff.png"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No such image.")
    return FileResponse(path, media_type="image/png")


@app.post("/api/session/{session_id}/visual-check")
async def post_visual_check(session_id: str, body: BaselineBody):
    """Compare what is on screen now against a stored baseline.

    The shot is taken at the baseline's own size, not at whatever the panel
    happens to be. In the workspace the browser is deliberately resized to fill
    its column, so a baseline captured that way would report "size changed" on
    every check — the one thing a visual test must never do is fail because the
    window moved.
    """
    target = require_target(session_id)
    try:
        target_size = _baseline_size(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    restore = None
    if target_size and hasattr(target, "set_viewport"):
        current = (target.config.get("width"), target.config.get("height"))
        if current != target_size:
            restore = current
            await target.set_viewport(*target_size)
            # Give the page a moment to re-lay out at the new width.
            await asyncio.sleep(0.5)

    try:
        shot = await target.screenshot()
        if not shot:
            raise HTTPException(status_code=503, detail="Could not read the screen.")

        png = base64.b64decode(shot)
        if body.update:
            saved = visual.save_baseline(body.name, png)
            return {**saved, "status": "updated", "passed": True,
                    "message": f"Baseline '{body.name}' replaced at "
                               f"{saved['width']}x{saved['height']}."}
        return visual.compare(body.name, png, body.threshold, body.tolerance)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if restore and all(restore):
            try:
                await target.set_viewport(*restore)
            except Exception:
                pass


def _baseline_size(body: BaselineBody):
    """The size this check should be taken at.

    An explicit size wins; otherwise an existing baseline's own size, so
    repeated checks are comparable; otherwise None, meaning "whatever the page
    is now" — which is correct only when creating a baseline.
    """
    if body.width and body.height:
        return (int(body.width), int(body.height))

    path = visual.baseline_path(body.name)  # raises ValueError on an empty name
    if body.update or not os.path.exists(path):
        return None

    from PIL import Image
    with Image.open(path) as image:
        return image.size


# --------------------------------------------------------------------------- #
# Trace & video for an interactive session
# --------------------------------------------------------------------------- #

@app.post("/api/session/{session_id}/trace/start")
async def post_trace_start(session_id: str):
    target = require_target(session_id)
    if not hasattr(target, "start_trace"):
        raise HTTPException(status_code=400, detail="Only web sessions can be traced.")
    return {"tracing": await target.start_trace()}


@app.post("/api/session/{session_id}/trace/stop")
async def post_trace_stop(session_id: str, run_id: Optional[str] = None):
    """Stop tracing and, when a run is named, attach the trace to it."""
    target = require_target(session_id)
    if not hasattr(target, "stop_trace"):
        raise HTTPException(status_code=400, detail="Only web sessions can be traced.")

    owner = run_id or f"session-{session_id}"
    directory = run_artifact_dir(owner)
    path = await target.stop_trace(os.path.join(directory, "trace.zip"))
    if path is None:
        return {"saved": False, "message": "Nothing was being traced."}

    artifact_id = None
    if run_id and storage.get_run(run_id):
        artifact_id = storage.add_artifact(
            run_id, "trace", os.path.relpath(path, directory),
            label="Playwright trace", size_bytes=os.path.getsize(path),
        )
    return {
        "saved": True, "artifactId": artifact_id,
        "sizeBytes": os.path.getsize(path),
        "hint": "Open it with: npx playwright show-trace <file>",
    }


# --------------------------------------------------------------------------- #
# Exploratory testing and one-click scans
# --------------------------------------------------------------------------- #

class ExploreBody(BaseModel):
    maxElements: int = explorer.DEFAULT_MAX_ELEMENTS
    maxSeconds: int = explorer.DEFAULT_MAX_SECONDS
    includeRisky: bool = False


@app.get("/api/session/{session_id}/suggestions")
async def get_suggestions(session_id: str):
    """What is worth testing on the page as it stands right now.

    Read off the current element tree rather than being a fixed list, so the
    first thing a user sees names controls that are actually on their screen.
    """
    target = require_target(session_id)
    snapshot = await target.snapshot()
    return {
        "page": suggestions.describe_page(snapshot),
        "suggestions": suggestions.for_snapshot(snapshot, target.kind),
    }


@app.post("/api/session/{session_id}/explore")
async def post_explore(session_id: str, body: ExploreBody):
    """Click every control on the page and report which ones do nothing."""
    target = require_target(session_id)
    stream = explorer.explore(
        target,
        max_elements=body.maxElements,
        max_seconds=body.maxSeconds,
        include_risky=body.includeRisky,
    )
    return StreamingResponse(stream, media_type="application/x-ndjson")


@app.post("/api/session/{session_id}/scan/links")
async def post_scan_links(session_id: str, limit: int = 80):
    target = require_target(session_id)
    result = await explorer.scan_links(target, limit=limit)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/session/{session_id}/scan/accessibility")
async def post_scan_accessibility(session_id: str):
    target = require_target(session_id)
    result = await explorer.scan_accessibility(target)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
