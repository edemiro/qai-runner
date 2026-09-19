"""Turn a live web page into the same semantic element tree the mobile side
produces, so the agent, the locator and the inspector do not care which one
they are looking at.

A real site's DOM is one to two orders of magnitude bigger than a mobile
screen's, and most of it is layout scaffolding. Everything here exists to cut
that down to the nodes a tester could actually act on or read.
"""

import uuid
from typing import Any, Dict, List, Optional

# Roles shared with mobile_dom, plus the two the web has and mobile does not.
INTERACTIVE_ROLES = {"button", "textbox", "checkbox", "switch", "link", "tab", "combobox", "radio"}

# Runs in the page. Returns a flat list in document order; the tree is rebuilt
# in Python so both platforms share one flattening implementation.
EXTRACT_JS = r"""
() => {
  const INTERACTIVE_TAGS = {
    A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox',
    SUMMARY: 'button', LABEL: 'text', OPTION: 'text',
  };
  const INPUT_ROLES = {
    checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button',
    reset: 'button', image: 'button', range: 'switch', file: 'button',
  };
  const ARIA_ROLES = {
    button: 'button', link: 'link', textbox: 'textbox', searchbox: 'textbox',
    checkbox: 'checkbox', radio: 'radio', switch: 'switch', tab: 'tab',
    combobox: 'combobox', listbox: 'combobox', menuitem: 'button',
    option: 'text', heading: 'text', img: 'image', separator: 'text',
  };
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'META', 'LINK', 'HEAD', 'BR', 'PATH', 'SVG', 'G']);

  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // Keep one screen of lookahead: the agent can scroll, and an element just
  // below the fold is a legitimate target once it does.
  const yLimit = vh * 2;

  function roleOf(el, style) {
    const aria = (el.getAttribute('role') || '').toLowerCase();
    if (ARIA_ROLES[aria]) return ARIA_ROLES[aria];

    const tag = el.tagName;
    if (tag === 'INPUT') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'hidden') return null;
      return INPUT_ROLES[type] || 'textbox';
    }
    if (tag === 'IMG') return 'image';
    if (INTERACTIVE_TAGS[tag]) return INTERACTIVE_TAGS[tag];
    if (el.isContentEditable) return 'textbox';
    if (/^H[1-6]$/.test(tag)) return 'text';
    if (el.hasAttribute('onclick') || (el.tabIndex >= 0 && tag !== 'BODY')) return 'button';
    if (style.cursor === 'pointer') return 'button';
    return 'text';
  }

  function ownText(el) {
    let text = '';
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) text += node.nodeValue;
    }
    return text.replace(/\s+/g, ' ').trim();
  }

  // Three ways an ancestor hides a child without the child's own computed
  // style or rect showing it: `opacity: 0` and `aria-hidden` do not inherit as
  // computed values, and a zero-sized clipping ancestor (the collapsed-drawer
  // pattern) leaves the child's own box intact. Checking only the element
  // itself lets all three through.
  function hiddenByAncestor(el) {
    const own = el.getBoundingClientRect();
    let node = el.parentElement;
    for (let depth = 0; node && node !== document.documentElement && depth < 20; depth++, node = node.parentElement) {
      if (node.hasAttribute('aria-hidden') && node.getAttribute('aria-hidden') !== 'false') return true;

      const style = getComputedStyle(node);
      if (parseFloat(style.opacity) === 0) return true;

      const clips = !(style.overflow === 'visible' && style.overflowX === 'visible' && style.overflowY === 'visible');
      if (clips) {
        const box = node.getBoundingClientRect();
        if (box.width < 2 || box.height < 2) return true;
        if (own.right <= box.left || own.left >= box.right ||
            own.bottom <= box.top || own.top >= box.bottom) return true;
      }
    }
    return false;
  }

  function cssPath(el, testId) {
    // The most stable locator a QA engineer can have is an explicit test id.
    if (testId) {
      for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
        const value = el.getAttribute(attr);
        if (value) {
          const selector = '[' + attr + '="' + CSS.escape(value) + '"]';
          try {
            if (document.querySelectorAll(selector).length === 1) return selector;
          } catch (e) { /* exotic value */ }
        }
      }
    }
    if (el.id && !/^[0-9]/.test(el.id)) {
      try {
        if (document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) {
          return '#' + CSS.escape(el.id);
        }
      } catch (e) { /* exotic id */ }
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id && !/^[0-9]/.test(node.id)) {
        parts.unshift('#' + CSS.escape(node.id));
        break;
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = [...parent.children].filter((s) => s.tagName === node.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  const nodes = [];
  const indexOf = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let el = document.body;

  do {
    if (SKIP_TAGS.has(el.tagName)) continue;

    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) continue;
    if (el.hasAttribute('aria-hidden') && el.getAttribute('aria-hidden') !== 'false') continue;

    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    if (rect.bottom < 0 || rect.top > yLimit || rect.right < 0 || rect.left > vw) continue;
    if (hiddenByAncestor(el)) continue;

    const role = roleOf(el, style);
    if (role === null) continue;

    const text = ownText(el);
    const label = el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                  el.getAttribute('title') || el.getAttribute('alt') || '';
    const testId = el.getAttribute('data-testid') || el.getAttribute('data-test') ||
                   el.getAttribute('data-cy') || el.getAttribute('data-qa') || '';
    const value = (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') ? (el.value || '') : '';
    const interactive = INTERACTIVE_ROLES_JS.has(role);

    // Layout-only nodes carry nothing a tester can target. Keep them out; their
    // informative children survive on their own and get re-parented in Python.
    if (!interactive && !text && !label && !testId && !value) continue;

    const parentIndex = (() => {
      let p = el.parentElement;
      while (p) {
        if (indexOf.has(p)) return indexOf.get(p);
        p = p.parentElement;
      }
      return -1;
    })();

    indexOf.set(el, nodes.length);
    nodes.push({
      role,
      tag: el.tagName.toLowerCase(),
      // Both are kept: the test id is the better locator, the html id is what
      // a developer recognises. Neither should shadow the other.
      id: el.id || null,
      testId: testId || null,
      text: text.slice(0, 200) || null,
      label: label.slice(0, 200) || null,
      value: value.slice(0, 200) || null,
      selector: cssPath(el, testId),
      bounds: {
        x1: Math.round(rect.left), y1: Math.round(rect.top),
        x2: Math.round(rect.right), y2: Math.round(rect.bottom),
      },
      enabled: !el.disabled,
      checked: el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio') ? el.checked : null,
      href: el.tagName === 'A' ? (el.getAttribute('href') || null) : null,
      parentIndex,
    });
  } while ((el = walker.nextNode()));

  return {
    nodes,
    viewport: { width: vw, height: vh },
    page: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
    scrollY: window.scrollY,
    url: location.href,
    title: document.title,
  };
}
"""

# The JS references this set by name; inject it so the two role lists cannot drift.
EXTRACT_JS = EXTRACT_JS.replace(
    "INTERACTIVE_ROLES_JS",
    "new Set(" + str(sorted(INTERACTIVE_ROLES)).replace("'", '"') + ")",
)


class WebElement:
    """One node of the page, shaped like MobileElement so the shared locator
    and inspector code can treat both identically."""

    def __init__(self, raw: Dict[str, Any]):
        self.role: str = raw["role"]
        self.tag: str = raw["tag"]
        self.test_id: Optional[str] = raw.get("testId")
        # `resource_id` is the mobile side's name for "the stable identifier".
        # A test id is the better one on the web, so it wins when present.
        self.resource_id: Optional[str] = self.test_id or raw.get("id")
        self.html_id: Optional[str] = raw.get("id")
        self.text: Optional[str] = raw.get("text") or raw.get("value")
        self.name: Optional[str] = raw.get("label")
        self.selector: str = raw["selector"]
        # `xpath` is the name the mobile side uses for "the structural locator".
        # Keeping the attribute name lets locator.py stay platform-neutral.
        self.xpath: str = raw["selector"]
        self.href: Optional[str] = raw.get("href")
        self.enabled: bool = bool(raw.get("enabled", True))
        self.checked: Optional[bool] = raw.get("checked")
        self.displayed = True
        self.visible = True
        self.clickable = self.role in INTERACTIVE_ROLES
        self.scrollable = False
        self.selected = False
        self.class_name = raw["tag"]
        self.element_id: Optional[str] = None
        self.children: List["WebElement"] = []

        b = raw["bounds"]
        width, height = b["x2"] - b["x1"], b["y2"] - b["y1"]
        self.bounds = {
            **b,
            "width": width,
            "height": height,
            "cx": b["x1"] + width // 2,
            "cy": b["y1"] + height // 2,
        }

    def is_actionable(self, screen_width: int = 0, screen_height: int = 0) -> bool:
        if not self.enabled or not self.bounds:
            return False
        if self.bounds["width"] <= 0 or self.bounds["height"] <= 0:
            return False
        # A node above the fold or one screen below it is reachable; the driver
        # scrolls it into view before acting.
        return True

    def describe(self) -> str:
        for candidate in (self.text, self.name, self.resource_id):
            if candidate:
                return candidate
        return self.role

    def _base(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {"class": self.role.capitalize(), "role": self.role}
        if self.resource_id:
            res["id"] = self.resource_id
        if self.test_id and self.html_id and self.test_id != self.html_id:
            res["htmlId"] = self.html_id
        if self.text:
            res["text"] = self.text
        if self.name:
            res["content-desc"] = self.name
        if self.href:
            res["href"] = self.href
        if self.checked is not None:
            res["checked"] = self.checked
        if not self.enabled:
            res["enabled"] = False
        return res

    def to_dict(self, include_children: bool = True) -> Dict[str, Any]:
        res = self._base()
        res["nativeClass"] = f"<{self.tag}>"
        res["xpath"] = self.selector
        res["actionable"] = self.is_actionable()
        res["clickable"] = self.clickable
        res["displayed"] = True
        b = self.bounds
        res["bounds"] = f"[{b['x1']},{b['y1']}][{b['x2']},{b['y2']}]"
        if include_children and self.children:
            res["children"] = [c.to_dict(True) for c in self.children]
        return res

    def to_llm_dict(self, include_children: bool = True) -> Dict[str, Any]:
        res = self._base()
        if self.clickable:
            res["interactive"] = True
        if include_children and self.children:
            res["children"] = [c.to_llm_dict(True) for c in self.children]
        return res


class WebSnapshot:
    """One capture of a page. Mirrors MobileDOMManager's public surface."""

    def __init__(self, payload: Dict[str, Any]):
        self.snapshot_id = uuid.uuid4().hex[:12]
        self.url: str = payload.get("url", "")
        self.title: str = payload.get("title", "")
        self.scroll_y: int = int(payload.get("scrollY", 0))

        viewport = payload.get("viewport") or {}
        self.screen_width = int(viewport.get("width", 1440))
        self.screen_height = int(viewport.get("height", 900))
        page = payload.get("page") or {}
        self.page_height = int(page.get("height", self.screen_height))

        raw_nodes: List[Dict[str, Any]] = payload.get("nodes", [])
        self._flat: List[WebElement] = [WebElement(node) for node in raw_nodes]
        # Built on first use by contains_text; a snapshot never changes.
        self._joined_text: Optional[str] = None

        self.elements_by_id: Dict[str, WebElement] = {}
        for index, element in enumerate(self._flat):
            element.element_id = f"el_{index}"
            self.elements_by_id[element.element_id] = element

        # Re-parent onto the nearest kept ancestor; dropped layout wrappers
        # simply collapse, which is the same flattening the mobile side does.
        roots: List[WebElement] = []
        for raw, element in zip(raw_nodes, self._flat):
            parent_index = raw.get("parentIndex", -1)
            if parent_index is None or parent_index < 0 or parent_index >= len(self._flat):
                roots.append(element)
            else:
                self._flat[parent_index].children.append(element)

        self.root_element: Optional[WebElement] = None
        if len(roots) == 1:
            self.root_element = roots[0]
        elif roots:
            synthetic = WebElement({
                "role": "text", "tag": "document", "id": None, "text": None,
                "label": self.title or self.url, "value": None, "selector": "html",
                "bounds": {"x1": 0, "y1": 0, "x2": self.screen_width, "y2": self.screen_height},
                "enabled": True, "checked": None, "href": None,
            })
            synthetic.element_id = "el_root"
            synthetic.children = roots
            self.elements_by_id["el_root"] = synthetic
            self.root_element = synthetic

    # --- shared surface -------------------------------------------------- #

    def get_all_elements(self) -> List[WebElement]:
        return self._flat

    def get_optimized_tree(self) -> Dict[str, Any]:
        if not self.root_element:
            return {}
        tree = self.root_element.to_dict(True)
        self._stamp_ids(tree, self.root_element)
        return tree

    def get_optimized_tree_for_llm(self) -> Dict[str, Any]:
        """The model's view of the page.

        Measured against a realistically heavy page, emitting one JSON object
        per kept node produced a tree *larger* than the raw HTML — a results
        list of 40 rows became 160 separate text objects whose per-node
        overhead dwarfed the `<span>` it replaced. Two rules fix that:
        a subtree with nothing interactive in it collapses to a single text
        string, and only actionable nodes carry an elementId, because those
        are the only ones the model can name in an action.
        """
        if not self.root_element:
            return {}
        collapsed = self._collapse(self.root_element)
        return collapsed if collapsed is not None else {}

    def _has_interactive(self, element: "WebElement") -> bool:
        if element.clickable:
            return True
        return any(self._has_interactive(child) for child in element.children)

    def _subtree_text(self, element: "WebElement") -> str:
        parts: List[str] = []
        for value in (element.text, element.name):
            if value and value not in parts:
                parts.append(value)
        for child in element.children:
            child_text = self._subtree_text(child)
            if child_text:
                parts.append(child_text)
        return " ".join(parts).strip()

    def _collapse(self, element: "WebElement") -> Optional[Dict[str, Any]]:
        if not self._has_interactive(element):
            text = self._subtree_text(element)
            if not text:
                return None
            # Long prose is context, not a target; a sentence of it is enough.
            return {"text": text[:300]}

        node = element.to_llm_dict(include_children=False)
        if element.clickable:
            node["elementId"] = element.element_id
        else:
            # Container: keep it for structure, but it is not a valid target.
            node.pop("interactive", None)

        children: List[Dict[str, Any]] = []
        pending_text: List[str] = []
        for child in element.children:
            collapsed = self._collapse(child)
            if collapsed is None:
                continue
            if set(collapsed.keys()) == {"text"}:
                # Merge runs of adjacent text so a row of four spans becomes
                # one string rather than four objects.
                pending_text.append(collapsed["text"])
                continue
            if pending_text:
                children.append({"text": " ".join(pending_text)[:300]})
                pending_text = []
            children.append(collapsed)
        if pending_text:
            children.append({"text": " ".join(pending_text)[:300]})

        if children:
            node["children"] = children
        return node

    def _stamp_ids(self, node: Dict[str, Any], element: WebElement) -> None:
        node["elementId"] = element.element_id
        for child_dict, child in zip(node.get("children", []), element.children):
            self._stamp_ids(child_dict, child)

    def contains_text(self, needle: str) -> bool:
        """Is this phrase on screen, as a person reading the screen would say?

        Matched against the page as one running text, not element by element.
        A phrase a tester writes down is a phrase they read off the screen, and
        the screen does not show them where one element stops: Turkish
        Airlines renders an airport as two siblings, "İstanbul Havalimanı" and
        "(IST)", so an assertion for "İstanbul Havalimanı (IST)" — which is
        what the screen says — failed on every scenario that checked a port
        had been selected, while the thing it asserted was plainly there.

        Elements are joined with a space and runs of whitespace collapse, so
        the phrase matches whether the page puts a space, a newline or nothing
        at all between the two. Whitespace inside the phrase is still required:
        this forgives how the markup was split, not what it says.
        """
        needle_norm = " ".join(needle.split()).lower()
        if not needle_norm:
            return False
        for element in self._flat:
            for value in (element.text, element.name):
                if value and needle_norm in " ".join(value.split()).lower():
                    return True
        return needle_norm in self._running_text()

    def _running_text(self) -> str:
        """Every node's text as one whitespace-normalised, lowercased string.

        A label repeated back to back is written once. The same words reach
        here twice over — a node's own text and its accessible name usually
        agree, and a wrapper repeats its child — and appending both put
        "İstanbul Havalimanı İstanbul Havalimanı" in the running text, which
        contains "Havalimanı İstanbul": a phrase in an order the screen never
        shows. Collapsing the repeat costs nothing and takes that with it.
        """
        if self._joined_text is None:
            parts: List[str] = []
            for element in self._flat:
                for value in (element.text, element.name):
                    if not value:
                        continue
                    normalised = " ".join(value.split())
                    if normalised and normalised != (parts[-1] if parts else None):
                        parts.append(normalised)
            self._joined_text = " ".join(parts).lower()
        return self._joined_text

    def visible_text(self) -> List[str]:
        out: List[str] = []
        for element in self._flat:
            for value in (element.text, element.name):
                if value and value not in out:
                    out.append(value)
        return out
