"""Web target tests, against a locally served fixture.

The fixture is deliberately heavy — nested layout wrappers, offscreen content,
a cookie banner, hidden nodes — because the whole point of the extractor is
that a real site's DOM is mostly scaffolding.
"""

import asyncio
import http.server
import json
import os
import tempfile
import threading
import unittest

import drivers.web as web_driver
from drivers.base import ActionResult
from drivers.web import WebTarget
from web_dom import WebSnapshot

FIXTURE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>Uçuş Ara — Fixture Air</title>
<style>
  body { font-family: sans-serif; margin: 0; }
  .wrap, .row, .col, .inner, .pad { display: block; }
  .hidden { display: none; }
  .invisible { visibility: hidden; }
  .transparent { opacity: 0; }
  .below { margin-top: 4000px; }
  .zero { width: 0; height: 0; overflow: hidden; }
  header, main, footer { padding: 12px; }
  button, input, select { font-size: 16px; padding: 8px; }
</style></head>
<body>
  <div id="cookie-banner" role="dialog" aria-label="Çerez tercihleri">
    <p>Bu site çerez kullanır.</p>
    <button id="accept-cookies" data-testid="cookie-accept">Kabul et</button>
    <button id="reject-cookies">Reddet</button>
  </div>

  <header>
    <div class="wrap"><div class="row"><div class="col"><div class="inner">
      <a href="/" id="logo" aria-label="Ana sayfa">Fixture Air</a>
      <nav>
        <a href="/flights">Uçuşlar</a>
        <a href="/checkin">Check-in</a>
        <a href="/miles">Miles</a>
      </nav>
    </div></div></div></div>
  </header>

  <main>
    <h1>Uçuş ara</h1>
    <form id="search-form">
      <div class="wrap"><div class="pad">
        <label for="from">Nereden</label>
        <input id="from" name="from" data-testid="origin" placeholder="İstanbul (IST)">
      </div></div>
      <div class="wrap"><div class="pad">
        <label for="to">Nereye</label>
        <input id="to" name="to" data-testid="destination" placeholder="Londra (LHR)">
      </div></div>
      <div class="pad">
        <label for="cabin">Kabin</label>
        <select id="cabin"><option>Economy</option><option>Business</option></select>
      </div>
      <div class="pad">
        <input type="checkbox" id="direct"><label for="direct">Sadece aktarmasız</label>
      </div>
      <button type="submit" id="search-btn" data-testid="search">Ara</button>
    </form>

    <!-- Three legs sharing one id each, as the multi-city booker really does.
         Invalid HTML, and the page ships it, so the extractor meets it. -->
    <div id="legs">
      <div class="leg"><span>1. Uçuş</span>
        <input id="legPort" aria-label="Nereden"></div>
      <div class="leg"><span>2. Uçuş</span>
        <input id="legPort" aria-label="Nereden"></div>
      <div class="leg"><span>3. Uçuş</span>
        <input id="legPort" aria-label="Nereden"></div>
    </div>

    <!-- Noise the extractor must drop -->
    <div class="hidden"><button id="ghost-hidden">Görünmez düğme</button></div>
    <div class="invisible"><button id="ghost-invisible">Gizli düğme</button></div>
    <div class="transparent"><button id="ghost-transparent">Şeffaf düğme</button></div>
    <div class="zero"><button id="ghost-zero">Sıfır boyut</button></div>
    <div aria-hidden="true"><button id="ghost-aria">Aria gizli</button></div>
    <span id="empty-wrapper"><span><span></span></span></span>

    <div class="below">
      <h2 id="far-below">Çok aşağıdaki başlık</h2>
      <button id="far-button">Aşağıdaki düğme</button>
    </div>
  </main>

  <footer><p id="legal">© Fixture Air</p></footer>
</body></html>
"""


def _serve(directory: str):
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=directory, **kw)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class TestWebTarget(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        with open(os.path.join(cls.tmpdir, "index.html"), "w", encoding="utf-8") as f:
            f.write(FIXTURE)
        cls.server = _serve(cls.tmpdir)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/index.html"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    async def asyncSetUp(self):
        # Every test in this class launches its own browser, and the consent
        # check waits CONSENT_TIMEOUT_MS for a banner before giving up. The
        # fixture page has none, so that wait was pure cost — two and a half
        # seconds per test, about a minute across the class, on a suite whose
        # browser tests already go red at random under load.
        self.target = await WebTarget.launch(
            self.url, viewport="desktop", headless=True, accept_consent=False,
        )

    async def asyncTearDown(self):
        await self.target.close()

    # --- extraction ------------------------------------------------------ #

    async def test_snapshot_reads_the_page(self):
        snapshot = await self.target.snapshot()
        self.assertIsNotNone(snapshot)
        self.assertIn("Uçuş Ara", snapshot.title)
        self.assertGreater(len(snapshot.get_all_elements()), 5)

    async def test_invisible_and_zero_size_nodes_are_dropped(self):
        """Every one of these is a real element in the DOM that a tester can
        never touch; leaving them in wastes tokens and invites bad targets."""
        snapshot = await self.target.snapshot()
        ids = {e.resource_id for e in snapshot.get_all_elements()}
        for ghost in ("ghost-hidden", "ghost-invisible", "ghost-transparent", "ghost-zero", "ghost-aria"):
            self.assertNotIn(ghost, ids, f"{ghost} should have been filtered out")

    async def test_layout_wrappers_are_dropped(self):
        snapshot = await self.target.snapshot()
        tags = [e.tag for e in snapshot.get_all_elements()]
        # The fixture nests four meaningless divs around the header links.
        self.assertLess(tags.count("div"), 6)

    async def test_far_below_the_fold_is_dropped(self):
        """4000px down is not reachable this turn; the agent scrolls first and
        gets a fresh snapshot."""
        snapshot = await self.target.snapshot()
        ids = {e.resource_id for e in snapshot.get_all_elements()}
        self.assertNotIn("far-button", ids)

    async def test_roles_match_the_mobile_vocabulary(self):
        snapshot = await self.target.snapshot()
        by_id = {e.resource_id: e for e in snapshot.get_all_elements()}
        self.assertEqual(by_id["search"].role, "button")
        self.assertEqual(by_id["origin"].role, "textbox")
        self.assertEqual(by_id["cabin"].role, "combobox")
        self.assertEqual(by_id["direct"].role, "checkbox")
        self.assertEqual(by_id["logo"].role, "link")

    async def test_a_test_id_outranks_the_html_id(self):
        """data-testid is the locator a QA engineer can rely on across
        redesigns, so it becomes the element's identity and its selector."""
        snapshot = await self.target.snapshot()
        by_id = {e.resource_id: e for e in snapshot.get_all_elements()}
        self.assertIn("origin", by_id)          # data-testid, not id="from"
        self.assertEqual(by_id["origin"].html_id, "from")
        self.assertEqual(by_id["origin"].selector, '[data-testid="origin"]')
        self.assertEqual(by_id["origin"].name, "İstanbul (IST)")  # placeholder becomes the label

    async def test_elements_without_a_test_id_fall_back_to_the_html_id(self):
        snapshot = await self.target.snapshot()
        by_id = {e.resource_id: e for e in snapshot.get_all_elements()}
        self.assertEqual(by_id["cabin"].selector, "#cabin")

    async def test_an_id_the_page_gave_to_three_elements_is_not_a_selector(self):
        """Multi-city search puts id="fromPort" on all three legs.

        The id was checked for uniqueness and rejected, and then the fallback
        path started at the element itself, found the same id and handed it
        straight back. So every leg's selector was leg one's: the model picked
        the second leg, the driver typed into the first, and six scenarios
        failed with "Lütfen seyahatinizin başlangıç ve varış noktalarını
        seçiniz" and the later legs empty.
        """
        snapshot = await self.target.snapshot()
        legs = [e for e in snapshot.get_all_elements()
                if e.html_id == "legPort"]
        self.assertEqual(len(legs), 3, "the fixture carries three of them")
        for leg in legs:
            self.assertNotEqual(leg.selector, "#legPort")
        self.assertEqual(len({leg.selector for leg in legs}), 3,
                         "one selector each, or two of them are the same leg")

    async def test_each_shared_id_selector_finds_only_its_own_element(self):
        """A distinct selector that still matches two elements is no better."""
        snapshot = await self.target.snapshot()
        for leg in [e for e in snapshot.get_all_elements()
                    if e.html_id == "legPort"]:
            found = await self.target.page.locator(leg.selector).count()
            self.assertEqual(found, 1, f"{leg.selector} matched {found}")

    async def test_the_role_is_not_also_sent_as_a_capitalised_class(self):
        """The tree goes out on every call, so anything in it that says nothing
        is paid for on every action of every run, for ever. `class` was `role`
        with a capital letter — measured on the Turkish Airlines home page,
        2,692 of the tree's 17,542 characters, 15% of every call."""
        snapshot = await self.target.snapshot()
        sent = json.dumps(snapshot.get_optimized_tree_for_llm(), ensure_ascii=False)
        self.assertIn('"role"', sent)
        self.assertNotIn('"class"', sent)

    async def test_the_inspector_still_gets_it(self):
        """It is read there, where the element list is colour-coded by kind."""
        snapshot = await self.target.snapshot()
        shown = json.dumps(snapshot.get_optimized_tree(), ensure_ascii=False)
        self.assertIn('"class"', shown)

    async def test_tree_has_the_same_shape_as_the_mobile_one(self):
        snapshot = await self.target.snapshot()
        tree = snapshot.get_optimized_tree()
        self.assertIn("role", tree)
        self.assertIn("elementId", tree)
        self.assertIn("children", tree)
        llm = snapshot.get_optimized_tree_for_llm()
        self.assertNotIn("xpath", llm)  # the verbose locator is stripped for the model

    async def test_element_ids_agree_between_the_two_trees(self):
        """The inspector stamps every node; the model tree stamps only what it
        can act on. Where both carry an id it must be the same one, or a click
        on el_9 in the UI and el_9 from the model hit different elements."""
        snapshot = await self.target.snapshot()

        def collect(node, out):
            if "elementId" in node:
                out[node["elementId"]] = node.get("id") or node.get("text")
            for child in node.get("children", []):
                collect(child, out)
            return out

        ui = collect(snapshot.get_optimized_tree(), {})
        model = collect(snapshot.get_optimized_tree_for_llm(), {})

        self.assertTrue(model, "the model tree offered no targets")
        for element_id, label in model.items():
            self.assertIn(element_id, ui)
            self.assertEqual(ui[element_id], label)

    # --- acting ---------------------------------------------------------- #

    async def test_click_dismisses_the_cookie_banner(self):
        snapshot = await self.target.snapshot()
        accept = next(e for e in snapshot.get_all_elements() if e.resource_id == "cookie-accept")
        result = await self.target.act("click", accept.element_id, None, None, snapshot.snapshot_id)
        self.assertTrue(result.ok, result.message)

    async def test_type_fills_an_input(self):
        snapshot = await self.target.snapshot()
        origin = next(e for e in snapshot.get_all_elements() if e.resource_id == "origin")
        result = await self.target.act("type", origin.element_id, None, "Ankara", snapshot.snapshot_id)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(await self.target.page.input_value("#from"), "Ankara")

    async def test_clear_empties_an_input(self):
        await self.target.page.fill("#from", "Ankara")
        snapshot = await self.target.snapshot()
        origin = next(e for e in snapshot.get_all_elements() if e.resource_id == "origin")
        result = await self.target.act("clear", origin.element_id, None, None, snapshot.snapshot_id)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(await self.target.page.input_value("#from"), "")

    async def test_assert_visible_does_not_click(self):
        snapshot = await self.target.snapshot()
        search = next(e for e in snapshot.get_all_elements() if e.resource_id == "search")
        result = await self.target.act("assert_visible", search.element_id, None, None, snapshot.snapshot_id)
        self.assertTrue(result.ok)
        self.assertIn("visible", result.message)

    async def test_a_stale_snapshot_id_is_refused(self):
        """The web equivalent of the mobile renumbering race: an id from a
        snapshot we no longer hold must not silently resolve against a new one."""
        snapshot = await self.target.snapshot()
        result = await self.target.act("click", "el_3", None, None, "deadbeefdead")
        self.assertFalse(result.ok)
        self.assertIn("no longer held", result.message)

    async def test_missing_element_reports_clearly(self):
        result = await self.target.act("click", None, "#does-not-exist", None, None)
        self.assertFalse(result.ok)
        self.assertIn("no longer on the page", result.message)

    async def test_scroll_moves_the_page(self):
        before = await self.target.page.evaluate("() => window.scrollY")
        result = await self.target.scroll("down")
        self.assertTrue(result.ok)
        after = await self.target.page.evaluate("() => window.scrollY")
        self.assertGreater(after, before)

    async def test_unknown_scroll_direction_is_refused(self):
        result = await self.target.scroll("sideways")
        self.assertFalse(result.ok)

    async def test_back_key_navigates_history(self):
        await self.target.page.goto(self.url + "?second=1", wait_until="domcontentloaded")
        result = await self.target.press_key("back")
        self.assertTrue(result.ok)
        self.assertNotIn("second=1", self.target.page.url)

    async def test_unsupported_key_is_refused(self):
        result = await self.target.press_key("volume_up")
        self.assertFalse(result.ok)

    async def test_screenshot_returns_base64_png(self):
        import base64
        shot = await self.target.screenshot()
        self.assertIsNotNone(shot)
        self.assertTrue(base64.b64decode(shot).startswith(b"\x89PNG"))

    async def test_element_at_finds_the_smallest_hit(self):
        snapshot = await self.target.snapshot()
        search = next(e for e in snapshot.get_all_elements() if e.resource_id == "search")
        found = await self.target.element_at(search.bounds["cx"], search.bounds["cy"])
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "search")

    async def test_a_live_page_reports_itself_alive(self):
        self.assertTrue(self.target.is_alive())

    async def test_a_closed_page_is_reported_dead(self):
        """Without this the UI cannot tell a dead page from a slow one, and
        freezes on the last frame it ever received."""
        await self.target.page.close()
        self.assertFalse(self.target.is_alive())

    async def test_a_dead_page_yields_no_screenshot(self):
        await self.target.page.close()
        self.assertIsNone(await self.target.screenshot())

    async def test_reopen_revives_a_dead_page_at_the_same_url(self):
        original = self.target.page.url
        session_id = self.target.session_id
        await self.target.page.close()
        self.assertFalse(self.target.is_alive())

        info = await self.target.reopen()

        self.assertTrue(self.target.is_alive())
        self.assertEqual(info["url"].rstrip("/"), original.rstrip("/"))
        # The session id must survive, or the run history detaches from it.
        self.assertEqual(self.target.session_id, session_id)
        self.assertIsNotNone(await self.target.screenshot())

    async def test_reopen_clears_stale_snapshots(self):
        await self.target.snapshot()
        await self.target.reopen()
        result = await self.target.act("click", "el_1", None, None, "deadbeefdead")
        self.assertFalse(result.ok)

    async def test_describe_reports_a_web_target(self):
        info = self.target.describe()
        self.assertEqual(info["kind"], "web")
        self.assertEqual(info["platform"], "Web")

    # --- the measurement that motivated the filter ----------------------- #

    async def test_filtering_removes_most_of_the_dom(self):
        raw = await self.target.page.evaluate("() => document.querySelectorAll('*').length")
        snapshot = await self.target.snapshot()
        kept = len(snapshot.get_all_elements())
        ratio = kept / raw
        print(f"\n[dom] {raw} elements -> {kept} kept ({ratio:.0%})")
        self.assertLess(ratio, 0.6, "the extractor is not filtering enough to be worth it")

    async def test_the_model_tree_is_smaller_than_the_raw_html(self):
        """The measurement that shaped the extractor: emitting one JSON object
        per kept node once produced a tree 30% LARGER than the page it
        replaced. If this regresses, the web target costs more than it saves."""
        import json

        raw_html = len(await self.target.page.content())
        snapshot = await self.target.snapshot()
        tree = json.dumps(
            snapshot.get_optimized_tree_for_llm(), ensure_ascii=False, separators=(",", ":")
        )
        print(f"[tree] raw HTML {raw_html:,} chars -> model tree {len(tree):,} chars")
        self.assertLess(len(tree), raw_html)

    async def test_only_actionable_nodes_carry_an_element_id(self):
        """The model can only name a target it can act on; ids on prose are
        wasted tokens and invite it to click a paragraph."""
        snapshot = await self.target.snapshot()
        tree = snapshot.get_optimized_tree_for_llm()
        ids = []

        def walk(node):
            if "elementId" in node:
                ids.append(node["elementId"])
            for child in node.get("children", []):
                walk(child)

        walk(tree)
        self.assertTrue(ids, "no targets were offered to the model at all")
        for element_id in ids:
            self.assertTrue(
                snapshot.elements_by_id[element_id].clickable,
                f"{element_id} is not interactive but was offered as a target",
            )


class TestNavigationErrors(unittest.TestCase):
    """Chromium's navigation errors are accurate but unactionable. A user who
    sees only "ERR_HTTP2_PROTOCOL_ERROR" cannot know that their antivirus is
    intercepting TLS — which is what it means in practice almost every time."""

    def _explain(self, message):
        from main import _explain_navigation_failure
        return _explain_navigation_failure("https://example.test/", Exception(message))

    def test_protocol_error_names_both_real_causes(self):
        """The two causes are indistinguishable from the error alone, so the
        message must cover both bot protection and TLS scanning rather than
        blaming one of them."""
        text = self._explain("Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR at https://example.test/")
        self.assertIn("otomatik tarayıcıları engelleyen", text)
        self.assertIn("antivirüs", text)
        self.assertIn("istisnalara", text)

    def test_the_hint_avoids_unexplained_jargon(self):
        """A user who does not already know what "headless" means reads that
        word as something they have to fix, and asks how to add a head."""
        text = self._explain("net::ERR_HTTP2_PROTOCOL_ERROR")
        self.assertNotIn("headless", text.lower())
        self.assertIn("ekransız", text)

    def test_connection_reset_gets_the_same_hint(self):
        self.assertIn("antivirüs", self._explain("net::ERR_CONNECTION_RESET"))

    def test_dns_failure_is_named_as_such(self):
        text = self._explain("net::ERR_NAME_NOT_RESOLVED")
        self.assertIn("çözümlenemiyor", text)
        self.assertNotIn("antivirüs", text)

    def test_missing_browser_gives_the_install_command(self):
        text = self._explain("Executable doesn't exist at C:\\...\\chrome.exe")
        self.assertIn("playwright install chromium", text)

    def test_the_raw_browser_error_is_still_included(self):
        """The hint is a guess; the underlying error must stay visible so a
        wrong guess does not hide the real cause."""
        text = self._explain("net::ERR_HTTP2_PROTOCOL_ERROR at https://example.test/")
        self.assertIn("ERR_HTTP2_PROTOCOL_ERROR", text)

    def test_an_unrecognised_error_is_passed_through(self):
        text = self._explain("something entirely new went wrong")
        self.assertIn("something entirely new went wrong", text)

    def test_protocol_errors_are_recognised_as_a_headless_block(self):
        """These are the errors that warrant reopening the page visibly."""
        from main import _looks_like_headless_block
        for signature in ("net::ERR_HTTP2_PROTOCOL_ERROR", "net::ERR_CONNECTION_RESET",
                          "net::ERR_EMPTY_RESPONSE", "net::ERR_SSL_PROTOCOL_ERROR"):
            self.assertTrue(_looks_like_headless_block(Exception(signature)), signature)

    def test_dns_and_timeout_failures_do_not_trigger_a_visible_retry(self):
        """Reopening a visible browser cannot fix a typo'd hostname; it would
        just pop a window on the user's desktop for nothing."""
        from main import _looks_like_headless_block
        for signature in ("net::ERR_NAME_NOT_RESOLVED", "net::ERR_CONNECTION_REFUSED",
                          "Timeout 30000ms exceeded"):
            self.assertFalse(_looks_like_headless_block(Exception(signature)), signature)


class TestWebSnapshotOffline(unittest.TestCase):
    """Shape checks that need no browser."""

    def _payload(self, nodes):
        return {
            "nodes": nodes, "viewport": {"width": 1440, "height": 900},
            "page": {"width": 1440, "height": 3000}, "scrollY": 0,
            "url": "https://example.test/", "title": "Example",
        }

    def _node(self, **kw):
        base = {
            "role": "text", "tag": "div", "id": None, "text": None, "label": None,
            "value": None, "selector": "div", "bounds": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
            "enabled": True, "checked": None, "href": None, "parentIndex": -1,
        }
        base.update(kw)
        return base

    def test_multiple_roots_get_a_synthetic_parent(self):
        snapshot = WebSnapshot(self._payload([
            self._node(id="a", text="A"),
            self._node(id="b", text="B"),
        ]))
        self.assertEqual(snapshot.root_element.element_id, "el_root")
        self.assertEqual(len(snapshot.root_element.children), 2)

    def test_children_attach_to_the_nearest_kept_ancestor(self):
        snapshot = WebSnapshot(self._payload([
            self._node(id="parent", text="P"),
            self._node(id="child", text="C", parentIndex=0),
        ]))
        self.assertEqual(snapshot.root_element.resource_id, "parent")
        self.assertEqual(snapshot.root_element.children[0].resource_id, "child")

    def test_empty_page_does_not_crash(self):
        snapshot = WebSnapshot(self._payload([]))
        self.assertEqual(snapshot.get_optimized_tree(), {})
        self.assertEqual(snapshot.visible_text(), [])

    def test_contains_text_is_case_insensitive(self):
        snapshot = WebSnapshot(self._payload([self._node(id="a", text="Welcome Back")]))
        self.assertTrue(snapshot.contains_text("welcome back"))
        self.assertFalse(snapshot.contains_text("goodbye"))

    def test_a_row_of_spans_collapses_into_one_string(self):
        """The regression that made the model tree bigger than the raw HTML:
        a results row of four sibling spans became four JSON objects."""
        snapshot = WebSnapshot(self._payload([
            self._node(id="card", role="text", tag="div"),
            self._node(text="TK1000", parentIndex=0),
            self._node(text="08:15", parentIndex=0),
            self._node(text="IST → LHR", parentIndex=0),
            self._node(text="2500 TL", parentIndex=0),
            self._node(id="select", role="button", tag="button", text="Seç", parentIndex=0),
        ]))
        tree = snapshot.get_optimized_tree_for_llm()
        children = tree["children"]
        self.assertEqual(len(children), 2, f"expected one text run + one button, got {children}")
        self.assertEqual(children[0], {"text": "TK1000 08:15 IST → LHR 2500 TL"})
        self.assertEqual(children[1]["id"], "select")

    def test_a_subtree_with_nothing_interactive_becomes_a_single_text_node(self):
        snapshot = WebSnapshot(self._payload([
            self._node(id="footer", role="text", tag="footer"),
            self._node(text="Gizlilik", parentIndex=0),
            self._node(text="Şartlar", parentIndex=0),
        ]))
        tree = snapshot.get_optimized_tree_for_llm()
        self.assertEqual(set(tree.keys()), {"text"})
        self.assertIn("Gizlilik", tree["text"])

    def test_prose_is_truncated_rather_than_sent_whole(self):
        snapshot = WebSnapshot(self._payload([self._node(id="a", text="x" * 5000)]))
        tree = snapshot.get_optimized_tree_for_llm()
        self.assertLessEqual(len(tree["text"]), 300)


class TellingNamesakesApart(unittest.TestCase):
    """Two controls with one name between them.

    Multi-city search puts three legs on one screen and the page gives every
    leg's port fields the same id and the same aria-label, so the model was
    handed six fields under two names. Six runs failed that way, the page
    answering "Lütfen seyahatinizin başlangıç ve varış noktalarını seçiniz"
    with the later legs still empty.
    """

    def _payload(self, nodes):
        return {
            "nodes": nodes, "viewport": {"width": 1440, "height": 900},
            "page": {"width": 1440, "height": 3000}, "scrollY": 0,
            "url": "https://example.test/", "title": "Example",
        }

    def _node(self, top, left=300, width=350, height=60, **kw):
        base = {
            "role": "text", "tag": "div", "id": None, "text": None,
            "label": None, "value": None, "selector": "div",
            "bounds": {"x1": left, "y1": top,
                       "x2": left + width, "y2": top + height},
            "enabled": True, "checked": None, "href": None, "parentIndex": -1,
        }
        base.update(kw)
        return base

    def _field(self, top, **kw):
        return self._node(top, role="combobox", tag="input",
                          selector="#fromPort", id="fromPort",
                          label="Nereden", **kw)

    def _legs(self):
        """Three legs, each a header, an origin field and a destination."""
        nodes = []
        for index, top in enumerate((100, 200, 300), start=1):
            nodes.append(self._node(top + 20, left=180, width=70, height=20,
                                    text=f"{index}. Uçuş"))
            nodes.append(self._field(top))
            nodes.append(self._node(top, left=700, width=350, height=60,
                                    role="combobox", tag="input",
                                    selector="#toPort", id="toPort",
                                    label="Nereye"))
        return nodes

    def test_each_leg_is_named_by_its_own_row(self):
        snapshot = WebSnapshot(self._payload(self._legs()))
        origins = [e for e in snapshot.get_all_elements()
                   if e.resource_id == "fromPort"]
        self.assertEqual([e.describe() for e in origins],
                         ["Nereden (1. Uçuş)", "Nereden (2. Uçuş)",
                          "Nereden (3. Uçuş)"])

    def test_the_model_is_told_which_one_it_is_looking_at(self):
        snapshot = WebSnapshot(self._payload(self._legs()))
        tree = json.dumps(snapshot.get_optimized_tree_for_llm(),
                          ensure_ascii=False)
        for leg in ("1. Uçuş", "2. Uçuş", "3. Uçuş"):
            self.assertIn(f'"within": "{leg}"', tree)

    def test_one_of_a_kind_is_left_alone(self):
        """A screen with a single "Nereden" keeps that name.

        Qualifying it anyway would rename every control every recording was
        made against, for no gain: there is nothing to tell it apart from.
        """
        snapshot = WebSnapshot(self._payload([
            self._node(120, left=180, width=70, height=20, text="1. Uçuş"),
            self._field(100),
        ]))
        field = next(e for e in snapshot.get_all_elements()
                     if e.resource_id == "fromPort")
        self.assertEqual(field.describe(), "Nereden")
        self.assertNotIn("within", field.to_llm_dict())

    def test_a_field_and_its_own_label_are_one_control(self):
        """The wrapper carrying the accessible name sits inside the input.

        Counted as two, the row header beside them looks like it describes
        more than one place and tells nothing apart — which is how the first
        attempt at this left every field unqualified.
        """
        nodes = self._legs()
        nodes.append(self._node(210, left=320, width=60, height=20,
                                role="combobox", tag="span",
                                selector="#labelNereden", label="Nereden"))
        snapshot = WebSnapshot(self._payload(nodes))
        second = [e.describe() for e in snapshot.get_all_elements()
                  if e.selector in ("#fromPort", "#labelNereden")
                  and e.bounds["y1"] in (200, 210)]
        self.assertEqual(second, ["Nereden (2. Uçuş)", "Nereden (2. Uçuş)"])

    def test_nothing_to_tell_them_apart_means_no_name_invented(self):
        """Two identical fields with nothing written beside either.

        A qualifier that does not distinguish is worse than none: it reads as
        though the screen answered the question when it did not.
        """
        snapshot = WebSnapshot(self._payload([
            self._field(100), self._field(200),
        ]))
        for field in snapshot.get_all_elements():
            self.assertEqual(field.describe(), "Nereden")


class LaunchArgsTests(unittest.TestCase):
    """Where the browser window goes when a site refuses a headless one.

    The headed fallback exists because some sites will not serve a headless
    browser at all — but it used to drop a real Chrome window on the user's
    desktop, when the page is supposed to live inside QAi.
    """

    # Asserted on the window flags rather than on the whole list: every
    # Chromium launch also carries the stealth flag, which is not about where
    # the window goes and is wanted headless too. These read "no window flags",
    # and checking for an empty list only ever stood in for that.
    WINDOW_FLAGS = ("--window-position", "CalculateNativeWinOcclusion")

    def assertNoWindowFlags(self, args):
        self.assertFalse(
            [a for a in args if any(flag in a for flag in self.WINDOW_FLAGS)],
            f"expected no window flags, got {args}",
        )

    def test_a_headless_launch_needs_no_window_flags(self):
        from drivers.web import _launch_args
        self.assertNoWindowFlags(
            _launch_args(headless=True, offscreen=True, browser_name="chromium"))

    def test_a_headed_launch_is_positioned_off_screen(self):
        from drivers.web import _launch_args
        args = _launch_args(headless=False, offscreen=True, browser_name="chromium")
        self.assertTrue(any("--window-position=-32000,-32000" == a for a in args))

    def test_the_window_is_kept_painting_while_off_screen(self):
        """An off-screen window counts as occluded, and Chromium stops painting
        one — which would freeze the screenshot stream the panel runs on."""
        from drivers.web import _launch_args
        args = _launch_args(headless=False, offscreen=True, browser_name="chromium")
        self.assertTrue(any("CalculateNativeWinOcclusion" in a for a in args))

    def test_off_screen_can_be_turned_off(self):
        from drivers.web import _launch_args
        self.assertNoWindowFlags(
            _launch_args(headless=False, offscreen=False, browser_name="chromium"))

    def test_every_chromium_launch_hides_that_it_is_automated(self):
        """Not a window flag, and wanted in all four combinations: without it
        the bot-protection layer fails the app's own API calls behind an error
        dialog, which reads as the feature being broken."""
        from drivers.web import _launch_args
        for headless in (True, False):
            for offscreen in (True, False):
                args = _launch_args(headless, offscreen, browser_name="chromium")
                self.assertIn("--disable-blink-features=AutomationControlled", args)

    def test_only_chromium_takes_these_flags(self):
        from drivers.web import _launch_args
        for browser in ("firefox", "webkit"):
            self.assertEqual(_launch_args(headless=False, offscreen=True, browser_name=browser), [])


class NavigationGuardTests(unittest.TestCase):
    """A refused connection must never look like a loaded page.

    Chromium answers a refusal by serving its own error document, so goto()
    returns normally and only the URL gives it away. Reporting that as success
    left sessions sitting on "Bu siteye ulaşılamıyor" with an empty element
    tree and suggestions generated from the error screen.
    """

    def test_an_error_document_is_recognised(self):
        from drivers.web import is_error_page
        self.assertTrue(is_error_page("chrome-error://chromewebdata/"))
        self.assertTrue(is_error_page("about:blank"))
        self.assertFalse(is_error_page("https://example.com/"))
        self.assertFalse(is_error_page(None))

    def test_a_refused_connection_is_treated_as_retryable(self):
        from drivers.web import looks_transient
        self.assertTrue(looks_transient("net::ERR_HTTP2_PROTOCOL_ERROR at https://x"))
        self.assertTrue(looks_transient("net::ERR_CONNECTION_RESET"))

    def test_a_real_mistake_is_not_retried(self):
        """A bad hostname will never succeed, so retrying only wastes time."""
        from drivers.web import looks_transient
        self.assertFalse(looks_transient("net::ERR_NAME_NOT_RESOLVED"))

    def test_a_navigation_that_lands_on_an_error_page_raises(self):
        from drivers.web import NavigationError, goto_with_retry

        class ErrorPage:
            url = "chrome-error://chromewebdata/"
            attempts = 0

            async def goto(self, url, **_kwargs):
                ErrorPage.attempts += 1

        with self.assertRaises(NavigationError):
            asyncio.run(goto_with_retry(ErrorPage(), "https://x.test/", attempts=2))
        self.assertEqual(ErrorPage.attempts, 2, "both attempts should be made")

    def test_a_transient_failure_is_retried_and_can_succeed(self):
        """The measured behaviour of the site that prompted this: the same URL
        fails, then loads on the next attempt."""
        from drivers.web import goto_with_retry

        class FlakyPage:
            def __init__(self):
                self.url = "chrome-error://chromewebdata/"
                self.calls = 0

            async def goto(self, url, **_kwargs):
                self.calls += 1
                if self.calls >= 2:
                    self.url = url

        page = FlakyPage()
        asyncio.run(goto_with_retry(page, "https://x.test/", attempts=3))
        self.assertEqual(page.calls, 2)
        self.assertEqual(page.url, "https://x.test/")


class ConsentBannerTests(unittest.TestCase):
    """Getting past the cookie banner without asking the model.

    49 of the 62 scenarios in the suite open with "Close the cookie consent
    banner by tapping Accept." — the same click on the same control, paid for
    with a model call and a screenshot every time. The driver does it now,
    before the agent is shown the page.

    The thing to get right is *which* button: the Turkish Airlines banner
    labels both of them "...kabul ediyorum", one accepting everything and one
    accepting only what is required, so a match on the word "kabul" silently
    changes what the rest of the run is testing.
    """

    class Button:
        def __init__(self, text="", visible=True, raises=None, element_id=None):
            self.text, self.visible, self.raises = text, visible, raises
            self.element_id = element_id
            self.clicks = 0

        @property
        def first(self):
            return self

        async def wait_for(self, **_kwargs):
            if not self.visible:
                raise RuntimeError("not visible")

        async def count(self):
            return 1 if self.visible else 0

        async def get_attribute(self, name):
            return self.element_id if name == "id" else None

        async def inner_text(self):
            return self.text

        async def click(self, **_kwargs):
            if self.raises:
                raise self.raises
            self.clicks += 1

    class Page:
        """A page that offers the banner through whichever lookup finds it."""

        def __init__(self, by_selector=None, by_role=None):
            self._by_selector, self._by_role = by_selector, by_role
            self.asked_names = []

        def locator(self, selector):
            self.asked_selector = selector
            return self._by_selector or ConsentBannerTests.Button(visible=False)

        def get_by_role(self, role, name=None):
            self.asked_names.append((role, name))
            return self._by_role or ConsentBannerTests.Button(visible=False)

    def test_the_known_button_is_clicked_by_id(self):
        from drivers.web import dismiss_consent
        button = self.Button(element_id="allowCookiesButton")
        page = self.Page(by_selector=button)
        result = asyncio.run(dismiss_consent(page))
        self.assertEqual(button.clicks, 1)
        # Names the button that was pressed, not the list it was found in: the
        # log line and the run's own record both read this.
        self.assertEqual(result, "#allowCookiesButton")

    def test_the_restrictive_button_is_not_among_the_known_ids(self):
        """The whole reason this goes by id: #notAllowCookiesButton reads
        "Sadece zorunlu çerezleri kabul ediyorum" and would match on text."""
        from drivers.web import CONSENT_ACCEPT_SELECTORS
        joined = " ".join(CONSENT_ACCEPT_SELECTORS)
        self.assertIn("#allowCookiesButton", joined)
        self.assertNotIn("notAllow", joined)

    def test_the_text_fallback_only_accepts_an_accept_everything_label(self):
        from drivers.web import CONSENT_ACCEPT_TEXT
        for label in ("Tüm çerezleri kabul ediyorum", "Tümünü kabul et",
                      "Bütün çerezleri kabul et", "Hepsini kabul ediyorum",
                      "Accept all cookies", "Allow all", "Accept All"):
            self.assertRegex(label, CONSENT_ACCEPT_TEXT, label)
        # A bare "Accept" on a two-button banner is as likely to be the
        # restrictive choice, so it is left to the agent rather than guessed.
        for label in ("Accept", "Kabul Et", "Onayla", "Ayarları değiştir",
                      "Çerez politikamızı inceleyin.", "Devam"):
            self.assertNotRegex(label, CONSENT_ACCEPT_TEXT, label)

    def test_a_refusal_is_never_clicked_however_it_is_worded(self):
        """The failure this exists to prevent: a label can carry both an
        all-word and an accept-word and still be the restrictive button."""
        from drivers.web import CONSENT_REFUSAL_TEXT
        for label in ("Sadece zorunlu çerezleri kabul ediyorum",
                      "Yalnızca gerekli çerezleri kabul et",
                      "Tümünü reddet", "Reject all", "Accept only necessary",
                      "Decline all cookies"):
            self.assertRegex(label, CONSENT_REFUSAL_TEXT, label)
        for label in ("Tüm çerezleri kabul ediyorum", "Accept all cookies"):
            self.assertNotRegex(label, CONSENT_REFUSAL_TEXT, label)

    def test_a_refusal_that_reaches_the_fallback_is_left_alone(self):
        from drivers.web import dismiss_consent
        refusal = self.Button("Sadece zorunlu çerezleri kabul ediyorum")
        page = self.Page(by_selector=None, by_role=refusal)
        self.assertIsNone(asyncio.run(dismiss_consent(page)))
        self.assertEqual(refusal.clicks, 0, "the restrictive button must not be clicked")

    def test_the_text_fallback_runs_only_when_no_id_matched(self):
        from drivers.web import dismiss_consent
        by_id = self.Button()
        page = self.Page(by_selector=by_id, by_role=self.Button("Accept all"))
        asyncio.run(dismiss_consent(page))
        self.assertEqual(page.asked_names, [], "the id matched; nothing else should be tried")

    def test_the_text_fallback_is_used_when_the_ids_are_absent(self):
        from drivers.web import dismiss_consent
        by_text = self.Button("Accept all cookies")
        page = self.Page(by_selector=None, by_role=by_text)
        result = asyncio.run(dismiss_consent(page))
        self.assertEqual(by_text.clicks, 1)
        self.assertEqual(result, "Accept all cookies")

    def test_a_page_with_no_banner_is_an_ordinary_outcome(self):
        """Every page after the first has no banner. It must cost nothing and
        say nothing."""
        from drivers.web import dismiss_consent
        self.assertIsNone(asyncio.run(dismiss_consent(self.Page())))

    def test_a_click_that_fails_does_not_bring_down_the_launch(self):
        """Losing a race with a re-render is normal. Whatever is left standing,
        the agent still handles the way it does today."""
        from drivers.web import dismiss_consent
        page = self.Page(by_selector=self.Button(raises=RuntimeError("detached")))
        self.assertIsNone(asyncio.run(dismiss_consent(page)))


class NavigateActionTests(unittest.TestCase):
    """Going to an address from inside a run.

    This is the action a scenario uses to say "go back to the home page and
    start again", and it was broken in a way nothing caught: `navigate` was
    defined twice on WebTarget, so the second definition replaced the first.
    The survivor took the URL raw and returned a plain dict, which meant a
    relative path — the form the agent is explicitly told it may use — reached
    Chromium as an address and came back "Cannot navigate to invalid URL". The
    exception was not caught either, so it killed the whole run instead of
    failing one step. A real scenario lost 15 steps of work to it.
    """

    def target(self, url="https://shop.test/search?q=1"):
        from drivers.web import WebTarget

        class Page:
            def __init__(self):
                self.url = url
                self.asked = []

            async def goto(self, to, **_kwargs):
                self.asked.append(to)
                self.url = to

        target = WebTarget.__new__(WebTarget)
        target.page = Page()
        target.config = {}
        target._snapshots = ["a stale snapshot"]
        return target

    def test_only_one_navigate_survives_on_the_class(self):
        """The bug itself: two defs, and Python keeps the last. Counted in the
        source because by the time it is an attribute the loser is gone."""
        import inspect
        import drivers.web
        source = inspect.getsource(drivers.web.WebTarget)
        self.assertEqual(source.count("    async def navigate("), 1)

    def test_a_relative_path_is_resolved_against_the_current_page(self):
        target = self.target()
        result = asyncio.run(target.navigate("/tr-tr/flights"))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(target.page.asked, ["https://shop.test/tr-tr/flights"])

    def test_the_site_root_is_a_valid_destination(self):
        """"/" is what "return to the home screen" turns into, and it is the
        exact value that produced the invalid-URL failure."""
        target = self.target()
        result = asyncio.run(target.navigate("/"))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(target.page.asked, ["https://shop.test/"])

    def test_a_bare_hostname_is_assumed_to_be_https(self):
        target = self.target()
        asyncio.run(target.navigate("example.test/x"))
        self.assertEqual(target.page.asked, ["https://example.test/x"])

    def test_an_absolute_url_is_left_alone(self):
        target = self.target()
        asyncio.run(target.navigate("http://other.test/y"))
        self.assertEqual(target.page.asked, ["http://other.test/y"])

    def test_it_returns_an_action_result_and_not_a_dict(self):
        """The agent reads .ok and .message off whatever comes back, so a dict
        here is an AttributeError mid-run — the second way the duplicate broke
        this, and the one that would have bitten even on a valid URL."""
        from drivers.base import ActionResult
        result = asyncio.run(self.target().navigate("/x"))
        self.assertIsInstance(result, ActionResult)

    def test_an_empty_address_fails_the_step_rather_than_the_run(self):
        target = self.target()
        result = asyncio.run(target.navigate("  "))
        self.assertFalse(result.ok)
        self.assertEqual(target.page.asked, [], "nothing should have been opened")

    def test_a_navigation_that_cannot_load_fails_the_step_rather_than_the_run(self):
        from drivers.web import WebTarget

        class DeadPage:
            url = "https://shop.test/"

            async def goto(self, to, **_kwargs):
                raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

        target = WebTarget.__new__(WebTarget)
        target.page = DeadPage()
        target.config = {}
        target._snapshots = []

        result = asyncio.run(target.navigate("https://nowhere.test/"))
        self.assertFalse(result.ok)
        self.assertIn("nowhere.test", result.message)

    def test_the_element_cache_is_dropped_on_arrival(self):
        """Every bound in it belongs to the page that just left; acting on one
        afterwards clicks whatever now occupies those coordinates."""
        target = self.target()
        asyncio.run(target.navigate("/somewhere-else"))
        self.assertEqual(target._snapshots, [])

    def test_a_permanent_failure_is_raised_without_retrying(self):
        from drivers.web import NavigationError, goto_with_retry

        class DeadPage:
            url = "about:blank"
            calls = 0

            async def goto(self, url, **_kwargs):
                DeadPage.calls += 1
                raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

        with self.assertRaises(NavigationError):
            asyncio.run(goto_with_retry(DeadPage(), "https://nope.test/", attempts=3))
        self.assertEqual(DeadPage.calls, 1, "a hostname that does not exist is not retried")

    def test_a_good_navigation_is_not_retried(self):
        from drivers.web import goto_with_retry

        class GoodPage:
            url = "about:blank"
            calls = 0

            async def goto(self, url, **_kwargs):
                GoodPage.calls += 1
                GoodPage.url = url

            async def wait_for_load_state(self, *_a, **_k):
                return None

        asyncio.run(goto_with_retry(GoodPage(), "https://ok.test/", attempts=3))
        self.assertEqual(GoodPage.calls, 1)

    def test_a_page_that_falls_back_to_an_error_while_settling_is_caught(self):
        """The one that slipped through in practice: navigation reports done on
        the real URL, then the site's own scripts land on an error page."""
        from drivers.web import NavigationError, goto_with_retry

        class LateFailurePage:
            def __init__(self):
                self.url = "about:blank"
                self.calls = 0

            async def goto(self, url, **_kwargs):
                self.calls += 1
                self.url = url          # looks fine at this instant

            async def wait_for_load_state(self, *_a, **_k):
                self.url = "chrome-error://chromewebdata/"   # ...and then is not

        page = LateFailurePage()
        with self.assertRaises(NavigationError) as caught:
            asyncio.run(goto_with_retry(page, "https://x.test/", attempts=2))
        self.assertIn("fell back", str(caught.exception))
        self.assertEqual(page.calls, 2)


class PointerTests(unittest.TestCase):
    """Press and release are separate operations.

    The panel used to send a finished click however the user actually pressed,
    so holding the mouse for five seconds delivered an instant click. Nothing
    that reacts to being held could be worked by hand.
    """

    def test_the_driver_exposes_press_move_and_release_separately(self):
        from drivers.web import WebTarget
        for name in ("pointer_down", "pointer_move", "pointer_up"):
            self.assertTrue(callable(getattr(WebTarget, name, None)), name)

    def test_release_does_not_require_coordinates(self):
        """A press that ends off the image still has to release the button."""
        import inspect
        from drivers.web import WebTarget
        signature = inspect.signature(WebTarget.pointer_up)
        self.assertIs(signature.parameters["x"].default, None)
        self.assertIs(signature.parameters["y"].default, None)

    def test_the_gesture_endpoint_forwards_each_phase_to_the_driver(self):
        """The three phases reach the driver as three distinct calls, in order."""
        import main

        calls = []

        class StubTarget:
            kind = "web"
            page = None

            async def pointer_down(self, x, y):
                calls.append(("down", x, y))
                return ActionResult(True, "ok")

            async def pointer_move(self, x, y):
                calls.append(("move", x, y))
                return ActionResult(True, "ok")

            async def pointer_up(self, x=None, y=None):
                calls.append(("up", x, y))
                return ActionResult(True, "ok")

        target = StubTarget()

        async def drive():
            for kind, x, y in (
                ("pointer_down", 10, 20), ("pointer_move", 30, 40), ("pointer_up", 50, 60),
            ):
                await main._web_gesture(target, main.GestureRequest(type=kind, x=x, y=y))

        asyncio.run(drive())
        self.assertEqual(calls, [("down", 10, 20), ("move", 30, 40), ("up", 50, 60)])

    def test_a_failed_press_is_reported_rather_than_swallowed(self):
        import main
        from fastapi import HTTPException

        class BrokenTarget:
            kind = "web"
            page = None

            async def pointer_down(self, x, y):
                return ActionResult(False, "the page is gone")

        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main._web_gesture(
                BrokenTarget(), main.GestureRequest(type="pointer_down", x=1, y=2),
            ))
        self.assertIn("the page is gone", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()


class NoBrowserWindowEverOpens(unittest.IsolatedAsyncioTestCase):
    """Opening a page must never put a browser window on the desktop.

    QAi shows the page inside its own panel. A site that refuses a background
    browser used to be reopened in a visible one, which meant a second window
    appearing over whatever the tester was doing — and those sites serve the
    same empty shell either way, so the window bought nothing.
    """

    async def _launch_kwargs_for(self, side_effect):
        from unittest.mock import AsyncMock, patch
        import main

        calls = []

        async def record(**kwargs):
            calls.append(kwargs)
            raise side_effect

        with patch.object(main.WebTarget, "launch", AsyncMock(side_effect=record)):
            with self.assertRaises(Exception):
                await main.create_web_session(
                    main.WebSessionRequest(url="https://example.test/")
                )
        return calls

    async def test_a_refusing_site_is_not_retried_with_a_window(self):
        calls = await self._launch_kwargs_for(
            Exception("Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR at https://example.test/")
        )
        self.assertEqual(len(calls), 1, "the page was opened more than once")
        self.assertTrue(calls[0]["headless"], "a visible browser window was launched")

    async def test_an_ordinary_failure_is_also_tried_only_once(self):
        calls = await self._launch_kwargs_for(Exception("net::ERR_NAME_NOT_RESOLVED"))
        self.assertEqual(len(calls), 1)



STICKY_FIXTURE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>Sabit alt bar</title>
<style>
  body { margin: 0; height: 3000px; font-family: sans-serif; }
  .bar { position: fixed; left: 0; right: 0; height: 60px; background: #222;
         color: #fff; display: flex; align-items: center; padding-left: 24px; }
  .b1 { bottom: 0; } .b2 { bottom: 70px; } .b3 { bottom: 140px; }
  .clip { overflow: hidden; height: 0; }
  .zero { overflow: hidden; height: 0; width: 0; }
  .gone { display: none; }
</style></head>
<body>
  <h1>Uçuş seçimi</h1>
  <div class="bar b1"><button id="plain-continue">Devam et</button></div>
  <div class="clip">
    <div class="bar b2"><button id="clipped-continue">Devam et 2</button></div>
  </div>
  <div class="zero">
    <div class="bar b3"><button id="collapsed-continue">Devam et 3</button></div>
  </div>
  <div class="gone"><button id="hidden-continue">Devam et 4</button></div>
</body></html>
"""


class TheStickyBarEveryCheckoutEndsWith(unittest.IsolatedAsyncioTestCase):
    """A `position: fixed` footer escapes the overflow of whatever container
    the markup put it in.

    These bars are built as collapsed drawers — an ancestor with
    `overflow: hidden` and no height — so reading visibility off the ancestor's
    box drops a button that is painted on the screen and takes clicks.

    Measured on the booking flow: the fare was chosen, "Devam et" went live in
    the bottom bar, and the agent scrolled nine times looking for a button that
    was never in the tree it had been given. The scenario failed at the step
    before passenger details, on a page where a person would simply have
    clicked.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        with open(os.path.join(cls.tmpdir, "index.html"), "w", encoding="utf-8") as f:
            f.write(STICKY_FIXTURE)
        cls.server = _serve(cls.tmpdir)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/index.html"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    async def asyncSetUp(self):
        self.target = await WebTarget.launch(
            self.url, viewport="desktop", headless=True, accept_consent=False,
        )
        self.ids = {
            element.resource_id
            for element in (await self.target.snapshot()).get_all_elements()
        }

    async def asyncTearDown(self):
        await self.target.close()

    async def test_a_plain_fixed_bar_is_in_the_tree(self):
        """The case that always worked, kept so a fix to the others cannot
        quietly break it."""
        self.assertIn("plain-continue", self.ids)

    async def test_a_bar_inside_a_collapsed_drawer_is_too(self):
        self.assertIn("clipped-continue", self.ids)

    async def test_and_one_inside_an_ancestor_with_no_size_at_all(self):
        self.assertIn("collapsed-continue", self.ids)

    async def test_something_genuinely_hidden_stays_out(self):
        """The ancestor walk exists for a reason, and rescuing the sticky bar
        must not turn it off: a display:none button is not on the screen by
        any reading, and offering it as a target is how a run clicks something
        nobody can see."""
        self.assertNotIn("hidden-continue", self.ids)


class WhenTheBrowserDoesNotStart(unittest.IsolatedAsyncioTestCase):
    """Starting a browser occasionally never finishes.

    Measured on this machine: forty launches, thirty-seven in half a second,
    three that never completed the handshake. No drift, nothing left over, no
    load — an isolated stall, about one launch in thirteen, which is what the
    endpoint protection on a corporate Windows box does to a new executable.

    It matters because a browser is started per scenario: at that rate four of
    a fifty-scenario execution stall, each sitting out Playwright's
    three-minute default. The stalls are independent — in the measurement the
    attempt straight after a stall succeeded all three times — so retrying is
    the whole fix.
    """

    class FakeLauncher:
        """Stalls for the first `stalls` attempts, then starts."""

        def __init__(self, stalls):
            self.stalls = stalls
            self.attempts = 0
            self.timeouts = []

        async def launch(self, **options):
            self.attempts += 1
            self.timeouts.append(options.get("timeout"))
            if self.attempts <= self.stalls:
                from playwright.async_api import Error as PlaywrightError
                raise PlaywrightError("BrowserType.launch: Timeout 30000ms exceeded.")
            return f"browser-{self.attempts}"

    async def test_a_stalled_launch_is_tried_again(self):
        launcher = self.FakeLauncher(stalls=1)
        browser = await web_driver._launch_browser(launcher, headless=True)
        self.assertEqual(browser, "browser-2")
        self.assertEqual(launcher.attempts, 2)

    async def test_it_gives_up_rather_than_retrying_for_ever(self):
        """A machine where every launch stalls has something else wrong with
        it, and a run that never ends tells nobody that."""
        launcher = self.FakeLauncher(stalls=99)
        with self.assertRaises(Exception):
            await web_driver._launch_browser(launcher, headless=True)
        self.assertEqual(launcher.attempts, web_driver.LAUNCH_ATTEMPTS)

    async def test_a_stall_costs_thirty_seconds_not_three_minutes(self):
        """Playwright's default is 180s, and four of those in one execution is
        twelve minutes of waiting for nothing."""
        launcher = self.FakeLauncher(stalls=0)
        await web_driver._launch_browser(launcher, headless=True)
        self.assertEqual(launcher.timeouts, [30_000])

    async def test_a_real_failure_is_not_retried(self):
        """A missing browser binary or a bad flag fails the same way three
        times over; retrying only delays the message that says so."""
        class Broken:
            def __init__(self):
                self.attempts = 0

            async def launch(self, **options):
                self.attempts += 1
                from playwright.async_api import Error as PlaywrightError
                raise PlaywrightError("Executable doesn't exist at ...")

        launcher = Broken()
        with self.assertRaises(Exception):
            await web_driver._launch_browser(launcher, headless=True)
        self.assertEqual(launcher.attempts, 1)
