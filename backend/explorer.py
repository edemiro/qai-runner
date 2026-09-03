"""Exploratory crawl: click everything on the page and report what actually did something.

The question this answers is the one a tester asks first on an unfamiliar
screen — "is any of this wired up?" — and it is the one an LLM agent is worst
at, because it is not a goal, it is an exhaustive sweep. So this runs
deterministically: no model call, no tokens, just click, observe, restore.

For each interactive element it records what the click did:

  navigated  the URL changed
  dialog     a modal or dialog appeared
  changed    the page's content changed
  request    it talked to the server
  dead       nothing at all happened — the finding worth having
  error      it logged a console error or a failed request
  blocked    it was skipped on purpose (see SKIP_PATTERNS)
  gone       it vanished before it could be clicked

A "dead" control is the classic defect this catches: a button that renders,
hovers, and does nothing.
"""

import asyncio
import json
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

import storage

# Clicking these can cost real money, destroy data, or log the tester out
# mid-crawl. Matched against the element's visible label, case-insensitively,
# in Turkish and English.
SKIP_PATTERNS = [
    # Leaving the session would end the crawl.
    r"\b(çıkış|cikis|oturumu kapat|log ?out|sign ?out)\b",
    # Destructive.
    r"\b(sil|kaldır|kaldir|delete|remove|discard|terminate)\b",
    # Costs money.
    r"\b(satın al|satin al|ödeme|odeme|öde|buy|purchase|checkout|pay now|place order)\b",
    # Sends something to a real person.
    r"\b(abone ol|subscribe|davet et|invite|share|paylaş|paylas)\b",
    # Account-level changes.
    r"\b(hesabı kapat|hesabi kapat|deactivate|close account|unsubscribe)\b",
]
SKIP_RE = re.compile("|".join(SKIP_PATTERNS), re.IGNORECASE)

# Schemes that leave the browser entirely.
SKIP_SCHEMES = ("mailto:", "tel:", "sms:", "file:", "ftp:")

# Roles a click is supposed to *do* something to. A textbox is interactive but
# not click-activated: clicking one only moves the caret, so probing it this way
# would report every input on the page as dead and bury the real findings.
CLICKABLE_ROLES = {"button", "link", "checkbox", "switch", "radio", "tab"}

DEFAULT_MAX_ELEMENTS = 40
DEFAULT_MAX_SECONDS = 240
# The same control repeated down a product grid tells us nothing new after the
# first couple, and would eat the whole budget.
MAX_PER_LABEL = 2

# How long to let the page react before deciding nothing happened. Too short
# and every async control looks dead; too long and a 40-element crawl drags.
SETTLE_SECONDS = 1.1


# A cheap, stable signature of "what the page looks like right now".
FINGERPRINT_JS = """
() => {
  const text = document.body ? document.body.innerText : '';
  // djb2 over the visible text: enough to notice a re-render, cheap enough to
  // run after every click.
  let hash = 5381;
  for (let i = 0; i < text.length; i++) hash = ((hash * 33) ^ text.charCodeAt(i)) >>> 0;
  const dialogs = document.querySelectorAll(
    '[role="dialog"], [role="alertdialog"], dialog[open], .modal.show, .modal[style*="block"]'
  ).length;
  // Toggling a checkbox changes nothing in innerText, so its state is tracked
  // separately — otherwise every working checkbox would be reported as dead.
  const toggles = Array.from(
    document.querySelectorAll('input[type="checkbox"], input[type="radio"], [role="switch"]')
  ).map((el) => (el.checked || el.getAttribute('aria-checked') === 'true' ? '1' : '0')).join('');
  return {
    url: location.href,
    title: document.title,
    hash,
    textLength: text.length,
    nodes: document.getElementsByTagName('*').length,
    dialogs,
    toggles,
    scrollY: window.scrollY,
  };
}
"""


def _label_of(element) -> str:
    """A name the reader can find on the page again.

    An icon-only control has no text at all, and reporting it as "button" makes
    the finding unactionable — so the selector stands in, which is the one
    thing that always identifies it.
    """
    for candidate in (element.text, element.name, element.resource_id):
        if candidate and candidate.strip():
            return candidate.strip()
    selector = (element.selector or "").strip()
    if selector and len(selector) <= 60:
        return f"<{element.tag}> {selector}"
    return f"<{element.tag}> (adsız {element.role})"


def should_skip(label: str, href: Optional[str], base_origin: str) -> Optional[str]:
    """Why this element must not be clicked, or None if it is safe.

    Being conservative here is the whole reason a crawl is usable on a real
    site: one stray click on "Delete account" and nobody runs it again.
    """
    if label and SKIP_RE.search(label):
        return "Atlandı: etiketi geri alınamaz ya da ücretli bir işlemi çağrıştırıyor."

    if href:
        lowered = href.strip().lower()
        if lowered.startswith(SKIP_SCHEMES):
            return "Atlandı: tarayıcının dışına çıkıyor."
        parsed = urlparse(href)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            if f"{parsed.scheme}://{parsed.netloc}" != base_origin:
                return f"Atlandı: site dışına ({parsed.netloc}) gidiyor."

    return None


def build_worklist(snapshot, base_origin: str, max_elements: int) -> List[Dict[str, Any]]:
    """Pick which elements to try, in the order a person would read them."""
    seen_labels: Dict[str, int] = {}
    seen_selectors: Set[str] = set()
    worklist: List[Dict[str, Any]] = []

    elements = [
        e for e in snapshot.get_all_elements()
        if e.role in CLICKABLE_ROLES and e.is_actionable()
    ]
    # Reading order: top to bottom, then left to right.
    elements.sort(key=lambda e: (e.bounds["y1"], e.bounds["x1"]))

    for element in elements:
        if element.selector in seen_selectors:
            continue
        seen_selectors.add(element.selector)

        label = _label_of(element)
        key = f"{element.role}:{label.lower()}"
        if label:
            seen_labels[key] = seen_labels.get(key, 0) + 1
            if seen_labels[key] > MAX_PER_LABEL:
                continue

        worklist.append({
            "selector": element.selector,
            "label": label or f"<{element.tag}>",
            "role": element.role,
            "href": element.href,
            "elementId": element.element_id,
            "skip": should_skip(label, element.href, base_origin),
        })

        if len(worklist) >= max_elements:
            break

    return worklist


def classify(before: Dict[str, Any], after: Dict[str, Any], requests: int) -> Dict[str, str]:
    """What did that click do?"""
    if before.get("url") != after.get("url"):
        return {"outcome": "navigated", "detail": f"Sayfayı değiştirdi: {after.get('url')}."}

    if after.get("dialogs", 0) > before.get("dialogs", 0):
        return {"outcome": "dialog", "detail": "Bir pencere (modal) açtı."}

    if before.get("toggles") != after.get("toggles"):
        return {"outcome": "changed", "detail": "Seçim durumunu değiştirdi."}

    if before.get("hash") != after.get("hash"):
        delta = after.get("textLength", 0) - before.get("textLength", 0)
        shape = f"{delta:+d} karakter" if delta else "aynı uzunluk, farklı içerik"
        return {"outcome": "changed", "detail": f"Sayfa içeriği değişti ({shape})."}

    if after.get("nodes") != before.get("nodes"):
        delta = after.get("nodes", 0) - before.get("nodes", 0)
        return {"outcome": "changed", "detail": f"DOM değişti ({delta:+d} eleman)."}

    if requests > 0:
        return {
            "outcome": "request",
            "detail": f"{requests} istek gönderdi ama ekranda hiçbir şey değişmedi.",
        }

    if after.get("scrollY") != before.get("scrollY"):
        return {"outcome": "changed", "detail": "Sayfayı kaydırdı."}

    return {
        "outcome": "dead",
        "detail": "Hiçbir şey olmadı: yönlendirme yok, içerik değişmedi, istek gitmedi.",
    }


def _event(kind: str, **payload) -> str:
    return json.dumps({"event": kind, **payload}, ensure_ascii=False) + "\n"


async def explore(
    target,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
    max_seconds: int = DEFAULT_MAX_SECONDS,
    include_risky: bool = False,
) -> AsyncGenerator[str, None]:
    """Click through the page and report what each control does.

    `include_risky` lifts the destructive-label guard. It exists because on a
    disposable staging environment the guard is exactly what stops you testing
    the delete button — but it is off by default for a reason.
    """
    page = getattr(target, "page", None)
    if page is None:
        yield _event("error", message="Keşif testi şimdilik yalnızca web sayfalarında çalışıyor.")
        return

    snapshot = await target.snapshot()
    if snapshot is None:
        yield _event("error", message="Sayfa okunamadı.")
        return

    base_url = snapshot.url
    parsed = urlparse(base_url)
    base_origin = f"{parsed.scheme}://{parsed.netloc}"
    worklist = build_worklist(snapshot, base_origin, max_elements)

    if not worklist:
        yield _event("error", message="Bu sayfada tıklanabilir bir eleman bulunamadı.")
        return

    run_id = None
    try:
        run_id = storage.create_run(
            goal=f"Keşif testi — {snapshot.title or base_url}",
            platform="Web", device_name=base_url, app_id=base_url,
            model="explorer", kind="web", tags=["exploratory"],
        )
        storage.rename_run(run_id, f"Keşif: {snapshot.title or base_url}"[:80])
    except Exception as exc:
        yield _event("warning", message=f"Bu tarama kaydedilmeyecek: {exc}")

    # Counting requests tells apart a control that silently talks to the server
    # from one that is genuinely not wired up at all.
    request_count = {"n": 0}

    def on_request(_request):
        request_count["n"] += 1

    page.on("request", on_request)

    started = time.time()
    tally: Dict[str, int] = {}
    findings: List[Dict[str, Any]] = []

    # What the page complains about all by itself. Every navigation re-triggers
    # these, so without a baseline every working link gets blamed for an error
    # that was already there before it was clicked.
    page_load_events = _events_of(target)
    baseline = baseline_signatures(page_load_events)
    if run_id and page_load_events:
        try:
            storage.add_page_events(run_id, [
                {**e, "level": "warning", "preexisting": True} for e in page_load_events
            ], step_idx=0)
        except Exception:
            pass

    yield _event(
        "explore_started", runId=run_id, url=base_url,
        title=snapshot.title, total=len(worklist),
        baselineErrors=len(page_load_events),
        baselineSamples=[describe_event(e) for e in page_load_events[:3]],
    )

    try:
        for index, item in enumerate(worklist, start=1):
            if time.time() - started > max_seconds:
                yield _event(
                    "warning",
                    message=(
                        f"{max_seconds} saniye sınırına ulaşıldı; "
                        f"{len(worklist) - index + 1} kontrol denenmeden kaldı."
                    ),
                )
                break

            result = await _probe(
                target, page, item, base_url, request_count, include_risky, baseline,
            )
            tally[result["outcome"]] = tally.get(result["outcome"], 0) + 1

            # Not carried into the event stream or the findings list: the
            # screenshot is large, and the raw events are filed separately.
            screenshot = result.pop("screenshot", None)
            events = result.pop("events", [])

            findings.append({**result, "label": item["label"], "role": item["role"]})

            if run_id:
                try:
                    storage.add_step(
                        run_id,
                        action="explore",
                        status="failed" if result["outcome"] in ("dead", "error") else "passed",
                        target=item["label"],
                        value=result["outcome"],
                        reason=item["role"],
                        message=result["detail"],
                        selector=item["selector"],
                        duration_ms=result.get("durationMs"),
                        screenshot=screenshot,
                    )
                    # Filed against this step, so the report can say which
                    # click produced which error instead of listing them all
                    # in a heap at the end of the run.
                    storage.add_page_events(run_id, events, step_idx=index)
                except Exception as exc:
                    print(f"[explore] could not record step {index}: {exc}")

            yield _event(
                "element_probed", index=index, total=len(worklist),
                label=item["label"], role=item["role"],
                events=[describe_event(e) for e in events[:5]],
                **result,
            )

    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass

        # Closing the run belongs here, not after the loop: an exception while
        # summarising — or the caller abandoning the stream halfway — would
        # otherwise leave the run stuck at "running" forever, which is worse
        # than a run with a rough verdict.
        if run_id:
            try:
                interim_dead = [f for f in findings if f["outcome"] == "dead"]
                interim_errors = [f for f in findings if f["outcome"] == "error"]
                storage.finish_run(
                    run_id,
                    "failed" if interim_dead or interim_errors else "passed",
                    verdict_note=_summarise(tally, interim_dead, interim_errors),
                )
            except Exception as exc:
                print(f"[explore] could not close the run: {exc}")
                try:
                    storage.finish_run(run_id, "failed", error=str(exc)[:200])
                except Exception:
                    pass

    dead = [f for f in findings if f["outcome"] == "dead"]
    errored = [f for f in findings if f["outcome"] == "error"]

    # A crawl that found dead controls has found a defect, so it fails — that
    # is the difference between a report and a test.
    status = "failed" if dead or errored else "passed"
    note = _summarise(tally, dead, errored)

    yield _event(
        "explore_finished", runId=run_id, status=status,
        tally=tally, summary=note,
        dead=[f["label"] for f in dead],
        errors=[f["label"] for f in errored],
    )


def _summarise(tally: Dict[str, int], dead: List[Dict], errored: List[Dict]) -> str:
    parts = []
    if dead:
        names = ", ".join(f'"{f["label"]}"' for f in dead[:4])
        parts.append(f"{len(dead)} kontrol hiçbir şey yapmıyor: {names}")
    if errored:
        parts.append(f"{len(errored)} kontrol hata verdi")
    if not parts:
        working = tally.get("navigated", 0) + tally.get("changed", 0) + tally.get("dialog", 0)
        return f"Denenen kontrollerin hepsi yanıt verdi ({working} tanesi görünür bir etki yarattı)."
    return " · ".join(parts) + "."


async def _probe(
    target, page, item: Dict[str, Any], base_url: str,
    request_count: Dict[str, int], include_risky: bool,
    baseline: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Click one element, work out what it did, and put the page back."""
    baseline = baseline or set()
    if item["skip"] and not include_risky:
        return {"outcome": "blocked", "detail": item["skip"], "durationMs": 0}

    started = time.time()

    # Every probe starts from the same page, or the results are not comparable.
    try:
        if page.url != base_url:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(0.4)
    except Exception as exc:
        return {
            "outcome": "error",
            "detail": f"Başlangıç sayfasına dönülemedi: {str(exc)[:160]}",
            "durationMs": int((time.time() - started) * 1000),
        }

    locator = page.locator(item["selector"]).first
    try:
        if await locator.count() == 0:
            return {
                "outcome": "gone",
                "detail": "Sırası geldiğinde eleman artık sayfada değildi.",
                "durationMs": int((time.time() - started) * 1000),
            }
    except Exception:
        return {
            "outcome": "gone", "detail": "Seçici artık hiçbir elemanla eşleşmiyor.",
            "durationMs": int((time.time() - started) * 1000),
        }

    seen_before = len(_events_of(target))
    try:
        before = await page.evaluate(FINGERPRINT_JS)
    except Exception as exc:
        return {
            "outcome": "error", "detail": f"Sayfa durumu okunamadı: {str(exc)[:140]}",
            "durationMs": int((time.time() - started) * 1000),
        }

    request_count["n"] = 0

    try:
        await locator.scroll_into_view_if_needed(timeout=4000)
        await locator.click(timeout=6000)
    except Exception as exc:
        detail = str(exc).split("\n")[0][:180]
        outcome = "blocked" if "intercepts pointer events" in detail else "error"
        return {
            "outcome": outcome,
            "detail": f"Tıklanamadı: {detail}",
            "durationMs": int((time.time() - started) * 1000),
        }

    # Let whatever it triggered actually happen.
    await asyncio.sleep(SETTLE_SECONDS)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=4000)
    except Exception:
        pass

    try:
        after = await page.evaluate(FINGERPRINT_JS)
    except Exception:
        # Evaluating fails while a navigation is still in flight, which is
        # itself the answer: the click navigated.
        after = {"url": page.url, "dialogs": 0, "hash": None, "textLength": 0, "nodes": 0}

    verdict = classify(before, after, request_count["n"])

    # Everything the page logged while this one click was in flight.
    new_events = _events_of(target)[seen_before:]

    blocking, noise = [], []
    for event in new_events:
        if event.get("level") != "error":
            noise.append(event)
        elif signature(event) in baseline:
            # The page was already raising this before anything was clicked, so
            # it is a property of the page, not of this control. Recorded, and
            # labelled, but it does not condemn the button.
            event["preexisting"] = True
            event["level"] = "warning"
            noise.append(event)
        else:
            blocking.append(event)

    if blocking:
        # Name the first one. A count alone cannot be acted on.
        first = describe_event(blocking[0])
        more = f" (+{len(blocking) - 1} tane daha)" if len(blocking) > 1 else ""
        verdict = {
            "outcome": "error",
            "detail": f"{verdict['detail']} Sayfa hatası: {first}{more}",
        }
    elif noise:
        # Recorded and shown, but it does not condemn the control: a 404 on a
        # tracking pixel, or an error the page was already raising, says
        # nothing about the button that was clicked.
        first = describe_event(noise[0])
        more = f" (+{len(noise) - 1} tane daha)" if len(noise) > 1 else ""
        label = "Sayfanın kendi hatası" if noise[0].get("preexisting") else "Not"
        verdict = {
            **verdict,
            "detail": f"{verdict['detail']} {label}: {first}{more}",
        }

    screenshot = None
    if verdict["outcome"] in ("dead", "error"):
        # Only the findings are worth a frame; one per element would make the
        # run enormous for no benefit.
        screenshot = await target.screenshot()

    await _restore(page, base_url, before, after)

    return {
        **verdict,
        "requests": request_count["n"],
        "durationMs": int((time.time() - started) * 1000),
        "screenshot": screenshot,
        # Carried out so the caller can file them against this step's index —
        # the report needs the errors themselves, not just how many there were.
        "events": new_events,
        "errorCount": len(blocking),
        "noticeCount": len(noise),
    }


def _events_of(target) -> List[Dict[str, Any]]:
    if not hasattr(target, "peek_events"):
        return []
    try:
        return target.peek_events()
    except Exception:
        return []


# Volatile parts of an error message: pointer addresses, ids, timestamps. Two
# occurrences of the same underlying error differ only in these.
_VOLATILE = re.compile(r"0x[0-9a-f]+|\b\d{3,}\b|[0-9a-f]{8,}", re.IGNORECASE)


def signature(event: Dict[str, Any]) -> str:
    """A stable identity for "this same error again".

    Sites raise the same handful of errors on every page load. Without this,
    each navigation re-triggers them and every link that works gets blamed for
    an error that was already there before it was clicked.
    """
    text = (event.get("text") or "")[:160].lower()
    return f"{event.get('kind')}:{_VOLATILE.sub('#', text).strip()}"


def baseline_signatures(events: List[Dict[str, Any]]) -> Set[str]:
    """What this page complains about on its own, before anything is clicked."""
    return {signature(event) for event in events}


def describe_event(event: Dict[str, Any]) -> str:
    """One readable line naming what actually went wrong.

    Reporting only a count — "1 sayfa hatası oluştu" — is unactionable: it
    tells you something is wrong without telling you what, where, or whether
    it has anything to do with the control you clicked.
    """
    kind = event.get("kind")
    # Console messages are frequently multi-line with blank lines between the
    # summary and the stack; collapsed, they fit on one readable row.
    text = re.sub(r"\s+", " ", (event.get("text") or "")).strip()
    url = event.get("url")
    where = ""
    if url:
        parsed = urlparse(url)
        # The path is what identifies the request; the host matters only when
        # it is somebody else's.
        shown = parsed.path or "/"
        if event.get("thirdParty"):
            shown = f"{parsed.netloc}{shown}"
        where = f" ({shown[:70]})"

    if kind == "httperror":
        resource = event.get("resourceType") or "istek"
        return f"{text}{where} — {resource}"
    if kind == "requestfailed":
        return f"İstek başarısız: {text}{where}"
    if kind == "pageerror":
        return f"JS hatası: {text[:180]}"
    return f"Konsol: {text[:180]}{where}"


async def _restore(page, base_url: str, before: Dict[str, Any], after: Dict[str, Any]) -> None:
    """Put the page back where the next probe expects to find it."""
    try:
        if after.get("dialogs", 0) > before.get("dialogs", 0):
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

        if page.url != base_url:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(0.3)
    except Exception:
        # The next probe re-navigates anyway; a failed tidy-up is not fatal.
        pass


# --------------------------------------------------------------------------- #
# Link scan
# --------------------------------------------------------------------------- #

async def scan_links(target, limit: int = 80, timeout: float = 12.0) -> Dict[str, Any]:
    """Request every link on the page and report the ones that are broken.

    Cheap, deterministic, and the single most common thing wrong with a page
    that otherwise looks fine.
    """
    import httpx

    page = getattr(target, "page", None)
    if page is None:
        return {"error": "Bağlantı taraması yalnızca web sayfalarında çalışır."}

    hrefs = await page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
          .map((a) => ({ href: a.href, text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 80) }))
    """)

    seen, targets = set(), []
    for entry in hrefs:
        href = entry["href"]
        if not href or href in seen:
            continue
        if not href.lower().startswith(("http://", "https://")):
            continue
        seen.add(href)
        targets.append(entry)
        if len(targets) >= limit:
            break

    results = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async def check(entry):
            try:
                # HEAD first; plenty of servers answer 405 to it, so fall back.
                response = await client.head(entry["href"])
                if response.status_code in (403, 405, 501):
                    response = await client.get(entry["href"])
                return {**entry, "status": response.status_code, "ok": response.status_code < 400}
            except Exception as exc:
                return {**entry, "status": None, "ok": False, "error": type(exc).__name__}

        results = await asyncio.gather(*[check(entry) for entry in targets])

    broken = [r for r in results if not r["ok"]]
    return {
        "checked": len(results),
        "broken": broken,
        "ok": len(results) - len(broken),
        "summary": (
            f"{len(broken)} kırık bağlantı bulundu ({len(results)} bağlantı denendi)."
            if broken else f"{len(results)} bağlantının hepsi çalışıyor."
        ),
    }


# --------------------------------------------------------------------------- #
# Accessibility scan
# --------------------------------------------------------------------------- #

# The checks below are the ones that are both unambiguous and high-impact: an
# image with no alt text, a control a screen reader cannot name, an input with
# no label. Contrast and focus-order checks are deliberately absent — they need
# rendering knowledge this cannot get right, and a wrong accessibility finding
# is worse than none.
A11Y_JS = """
() => {
  const findings = [];
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const describe = (el) => {
    const id = el.id ? `#${el.id}` : '';
    const cls = el.className && typeof el.className === 'string'
      ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '';
    return `<${el.tagName.toLowerCase()}${id}${cls}>`;
  };
  const accessibleName = (el) =>
    (el.getAttribute('aria-label')
      || (el.getAttribute('aria-labelledby')
          && (document.getElementById(el.getAttribute('aria-labelledby'))?.innerText || ''))
      || el.innerText
      || el.getAttribute('title')
      || el.getAttribute('alt')
      || '').trim();

  document.querySelectorAll('img').forEach((img) => {
    if (!visible(img)) return;
    if (img.getAttribute('alt') === null) {
      findings.push({ rule: 'image-alt', severity: 'serious', element: describe(img),
        detail: 'Görsel için alt metni yok; ekran okuyucu bunu okuyamaz.',
        hint: img.src ? img.src.split('/').pop().slice(0, 60) : '' });
    }
  });

  document.querySelectorAll('button, [role="button"], a[href]').forEach((el) => {
    if (!visible(el)) return;
    if (!accessibleName(el)) {
      findings.push({ rule: 'control-name', severity: 'critical', element: describe(el),
        detail: 'Kontrolün erişilebilir bir adı yok; sadece ikon olabilir.', hint: '' });
    }
  });

  document.querySelectorAll('input, select, textarea').forEach((el) => {
    if (!visible(el)) return;
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(el.type)) return;
    const labelled = el.labels && el.labels.length > 0;
    if (!labelled && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')) {
      findings.push({ rule: 'input-label', severity: 'serious', element: describe(el),
        detail: 'Form alanının etiketi yok; placeholder etiket yerine geçmez.',
        hint: el.placeholder || el.name || '' });
    }
  });

  const levels = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
    .filter(visible).map((h) => parseInt(h.tagName[1], 10));
  if (levels.length && levels[0] !== 1) {
    findings.push({ rule: 'heading-order', severity: 'moderate', element: `<h${levels[0]}>`,
      detail: `Sayfa h${levels[0]} ile başlıyor; ilk başlık h1 olmalı.`, hint: '' });
  }
  for (let i = 1; i < levels.length; i++) {
    if (levels[i] - levels[i - 1] > 1) {
      findings.push({ rule: 'heading-order', severity: 'moderate',
        element: `<h${levels[i]}>`,
        detail: `Başlık seviyesi h${levels[i - 1]} sonrası h${levels[i]}'e atlıyor.`, hint: '' });
      break;
    }
  }

  if (!document.documentElement.getAttribute('lang')) {
    findings.push({ rule: 'html-lang', severity: 'serious', element: '<html>',
      detail: 'Sayfanın dili belirtilmemiş (<html lang="tr">).', hint: '' });
  }
  if (!document.title || !document.title.trim()) {
    findings.push({ rule: 'page-title', severity: 'serious', element: '<title>',
      detail: 'Sayfanın başlığı yok.', hint: '' });
  }

  return findings;
}
"""

SEVERITY_ORDER = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}


async def scan_accessibility(target) -> Dict[str, Any]:
    page = getattr(target, "page", None)
    if page is None:
        return {"error": "Erişilebilirlik taraması yalnızca web sayfalarında çalışır."}

    try:
        findings = await page.evaluate(A11Y_JS)
    except Exception as exc:
        return {"error": f"Tarama çalıştırılamadı: {str(exc)[:160]}"}

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 9))
    by_rule: Dict[str, int] = {}
    for finding in findings:
        by_rule[finding["rule"]] = by_rule.get(finding["rule"], 0) + 1

    return {
        "findings": findings[:100],
        "total": len(findings),
        "byRule": by_rule,
        "summary": (
            f"{len(findings)} erişilebilirlik sorunu bulundu."
            if findings else "Temel erişilebilirlik kontrollerinden geçti."
        ),
    }
