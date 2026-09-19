import re
import uuid
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

# Regular expression to parse "[x1,y1][x2,y2]" bounds format
BOUNDS_REGEX = re.compile(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]')

# Roles that a user can meaningfully act on. Used by the LLM serializer to mark
# which nodes are candidates for a click/type action.
INTERACTIVE_ROLES = {"button", "textbox", "checkbox", "switch", "link", "tab"}


class MobileElement:
    """
    Represents a unified, semantic element in a mobile UI tree.
    Bridges the gap between Android and iOS representation.
    """
    def __init__(self, raw_node: ET.Element, platform: str = "Android", xpath: str = ""):
        self.platform = platform.lower()
        self.tag = raw_node.tag
        self.xpath = xpath
        self.element_id: Optional[str] = None

        # Original attributes
        self.attribs = raw_node.attrib

        # Simplified and unified semantic attributes
        self.class_name = self.attribs.get("class", self.tag)
        self.role = self._resolve_role(self.class_name, self.attribs)

        # Text/Label/Name attributes mapping
        self.text = self.attribs.get("text") or self.attribs.get("label") or self.attribs.get("value")
        if self.text:
            self.text = self.text.strip()

        self.name = self.attribs.get("content-desc") or self.attribs.get("name")
        if self.name:
            self.name = self.name.strip()

        # Resource or accessibility identifiers
        raw_res_id = self.attribs.get("resource-id")
        if raw_res_id:
            self.resource_id = raw_res_id.split(":id/")[-1] if ":id/" in raw_res_id else raw_res_id
        else:
            self.resource_id = None

        self.bounds = self._parse_bounds(self.attribs)

        # Interactive state flags
        self.displayed = self.attribs.get("displayed", "true").lower() != "false"
        self.visible = self.attribs.get("visible", "true").lower() != "false"
        self.enabled = self.attribs.get("enabled", "true").lower() != "false"
        self.clickable = self.attribs.get("clickable", "false").lower() == "true" or self.role in ["button", "textbox", "checkbox", "switch"]
        self.scrollable = self.attribs.get("scrollable", "false").lower() == "true"
        self.checked = self.attribs.get("checked", "").lower() == "true"
        self.selected = self.attribs.get("selected", "").lower() == "true"

        self.children: List['MobileElement'] = []

    def _resolve_role(self, class_name: str, attribs: Dict[str, str]) -> str:
        """
        Maps platform-specific class names to unified semantic roles.
        """
        name_lower = class_name.lower()

        # Android mapping
        if "button" in name_lower or "imagebutton" in name_lower:
            return "button"
        if "edittext" in name_lower or "textfield" in name_lower:
            return "textbox"
        if "checkbox" in name_lower:
            return "checkbox"
        if "switch" in name_lower or "togglebutton" in name_lower:
            return "switch"
        if "textview" in name_lower:
            return "text"
        if "imageview" in name_lower:
            return "image"

        # iOS mapping
        if "button" in name_lower:
            return "button"
        if "textfield" in name_lower or "securetextfield" in name_lower or "textview" in name_lower:
            return "textbox"
        if "checkbox" in name_lower:
            return "checkbox"
        if "switch" in name_lower:
            return "switch"
        if "statictext" in name_lower:
            return "text"
        if "image" in name_lower:
            return "image"

        # Fallback to general clickable or tag name
        if attribs.get("clickable") == "true":
            return "button"

        return class_name.split(".")[-1].lower()

    def _parse_bounds(self, attribs: Dict[str, str]) -> Optional[Dict[str, int]]:
        """
        Parses the "[x1,y1][x2,y2]" bounds string or reconstructs it from individual fields (iOS style).
        Returns a dict with key/values: x1, y1, x2, y2, width, height, cx (center x), cy (center y)
        """
        bounds_str = attribs.get("bounds")

        # If bounds is not directly provided but individual coords exist (common in some iOS XMLs)
        if not bounds_str:
            x = attribs.get("x")
            y = attribs.get("y")
            width = attribs.get("width")
            height = attribs.get("height")
            if x is not None and y is not None and width is not None and height is not None:
                try:
                    x1 = int(x)
                    y1 = int(y)
                    w = int(width)
                    h = int(height)
                    bounds_str = f"[{x1},{y1}][{x1+w},{y1+h}]"
                except ValueError:
                    pass

        if not bounds_str:
            return None

        match = BOUNDS_REGEX.match(bounds_str)
        if match:
            x1, y1, x2, y2 = map(int, match.groups())
            width = x2 - x1
            height = y2 - y1
            return {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "width": width,
                "height": height,
                "cx": x1 + (width // 2),
                "cy": y1 + (height // 2)
            }
        return None

    def is_actionable(self, screen_width: int = 1080, screen_height: int = 2400) -> bool:
        """
        Check if the element is actionable (visible, enabled, and within viewport bounds).
        """
        if not self.displayed or not self.visible or not self.enabled:
            return False

        if not self.bounds:
            return False

        # Zero-area elements can never receive a touch.
        if self.bounds["width"] <= 0 or self.bounds["height"] <= 0:
            return False

        # Viewport constraint checking: centers must be within device screen range
        cx, cy = self.bounds["cx"], self.bounds["cy"]
        if cx < 0 or cx > screen_width or cy < 0 or cy > screen_height:
            return False

        return True

    def describe(self) -> str:
        """Human-readable label used in step logs and exported scripts."""
        for candidate in (self.text, self.name, self.resource_id):
            if candidate:
                return candidate
        return self.role

    def to_dict(self, include_children: bool = True) -> Dict[str, Any]:
        """
        Serializes the element for the frontend inspector. `class` stays the
        capitalized role for backwards compatibility; `role` is the canonical
        lowercase value the UI uses for colour coding, and `nativeClass` keeps
        the original platform class name for reference.
        """
        res = {
            "class": self.role.capitalize(),
            "role": self.role,
            "nativeClass": self.class_name,
            "xpath": self.xpath,
            "actionable": self.is_actionable(),
            "clickable": self.clickable,
            "enabled": self.enabled,
            "displayed": self.displayed and self.visible,
        }

        if self.resource_id:
            res["id"] = self.resource_id
        if self.text:
            res["text"] = self.text
        if self.name:
            res["content-desc"] = self.name
        if self.scrollable:
            res["scrollable"] = True
        if self.checked:
            res["checked"] = True
        if self.selected:
            res["selected"] = True
        if self.bounds:
            res["bounds"] = f"[{self.bounds['x1']},{self.bounds['y1']}][{self.bounds['x2']},{self.bounds['y2']}]"

        if include_children and self.children:
            res["children"] = [child.to_dict(include_children=True) for child in self.children]

        return res

    def to_llm_dict(self, include_children: bool = True) -> Dict[str, Any]:
        """
        Serializes the element to an LLM-optimized dictionary without verbose xpath.
        """
        res = {
            "class": self.role.capitalize(),
        }

        if self.resource_id:
            res["id"] = self.resource_id
        if self.text:
            res["text"] = self.text
        if self.name:
            res["content-desc"] = self.name
        if self.role in INTERACTIVE_ROLES or self.clickable:
            res["interactive"] = True
        if self.scrollable:
            res["scrollable"] = True
        if self.checked:
            res["checked"] = True
        if not self.enabled:
            res["enabled"] = False
        if self.bounds:
            res["bounds"] = f"[{self.bounds['x1']},{self.bounds['y1']}][{self.bounds['x2']},{self.bounds['y2']}]"

        if include_children and self.children:
            res["children"] = [child.to_llm_dict(include_children=True) for child in self.children]

        return res


class MobileDOMManager:
    """
    Manages parsing, semantic querying, and LLM optimization for mobile UI trees.

    Each manager carries a `snapshot_id`. Element ids (`el_0`, `el_1`, ...) are
    only meaningful within one snapshot, so callers that resolve an id must pass
    the snapshot it came from — otherwise an interleaved refresh can silently
    renumber the tree and an action lands on the wrong element.
    """
    def __init__(self, xml_source: str, platform: str = "Android", screen_width: int = 1080, screen_height: int = 2400):
        self.xml_source = xml_source
        self.platform = platform
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.snapshot_id = uuid.uuid4().hex[:12]

        self.element_counter = 0
        self.elements_by_id: Dict[str, MobileElement] = {}
        self.root_element: Optional[MobileElement] = None
        self._flat_cache: Optional[List[MobileElement]] = None

        self._parse_tree()
        self._assign_ids()

    def _parse_tree(self):
        try:
            root_node = ET.fromstring(self.xml_source.encode('utf-8'))
            self.root_element = self._build_semantic_tree(root_node, f"/{root_node.tag}[1]")
        except Exception as e:
            print(f"Error parsing mobile XML source: {e}")
            self.root_element = None

    def _build_semantic_tree(self, node: ET.Element, xpath: str) -> Optional[MobileElement]:
        elem = MobileElement(node, self.platform, xpath)

        # Process children
        tag_counts = {}
        for child in node:
            child_tag = child.tag
            tag_counts[child_tag] = tag_counts.get(child_tag, 0) + 1
            child_xpath = f"{xpath}/{child_tag}[{tag_counts[child_tag]}]"

            parsed_child = self._build_semantic_tree(child, child_xpath)
            if parsed_child:
                elem.children.append(parsed_child)

        return elem

    def _assign_ids(self):
        """
        Assign stable element ids once, at construction time, in a single
        document-order pass. Previously each serializer reset the counter, so
        two calls on the same manager could hand out different ids.
        """
        self.element_counter = 0
        self.elements_by_id.clear()
        for elem in self.get_all_elements():
            element_id = f"el_{self.element_counter}"
            self.element_counter += 1
            elem.element_id = element_id
            self.elements_by_id[element_id] = elem

    def _optimize(self, serializer: str) -> Dict[str, Any]:
        if not self.root_element:
            return {}

        def _walk(elem: MobileElement) -> Optional[Dict[str, Any]]:
            is_visible = elem.displayed and elem.visible
            has_vital_info = is_visible and bool(elem.resource_id or elem.text or elem.name or elem.clickable)

            optimized_children = []
            for child in elem.children:
                opt_child = _walk(child)
                if opt_child:
                    optimized_children.append(opt_child)

            if optimized_children:
                # Flatten empty containers that wrap a single child.
                if not has_vital_info and len(optimized_children) == 1:
                    return optimized_children[0]

                res = getattr(elem, serializer)(include_children=False)
                res["elementId"] = elem.element_id
                res["children"] = optimized_children
                return res

            # Leaf nodes are dropped unless they carry information.
            if not has_vital_info:
                return None

            res = getattr(elem, serializer)(include_children=False)
            res["elementId"] = elem.element_id
            return res

        return _walk(self.root_element) or {}

    def get_optimized_tree(self) -> Dict[str, Any]:
        """Clean, flattened tree for the frontend inspector."""
        return self._optimize("to_dict")

    def get_optimized_tree_for_llm(self) -> Dict[str, Any]:
        """Same shape, minus the verbose xpath, for the model prompt."""
        return self._optimize("to_llm_dict")

    # --- Playwright-like Locator APIs ---

    def get_all_elements(self) -> List[MobileElement]:
        """
        Returns a flat list of all parsed elements, in document order.
        """
        if self._flat_cache is not None:
            return self._flat_cache

        flat_list: List[MobileElement] = []

        def _traverse(elem: Optional[MobileElement]):
            if elem:
                flat_list.append(elem)
                for child in elem.children:
                    _traverse(child)

        _traverse(self.root_element)
        self._flat_cache = flat_list
        return flat_list

    def get_by_role(self, role: str, name: Optional[str] = None) -> List[MobileElement]:
        """
        Locates elements by their unified semantic role and matching text or name.
        Example: get_by_role("button", "Giriş Yap")
        """
        matches = []
        role_lower = role.lower()
        name_lower = name.lower() if name else None

        for elem in self.get_all_elements():
            if elem.role == role_lower:
                if name_lower is None:
                    matches.append(elem)
                else:
                    # Match name against text, content-desc, name or resource-id
                    elem_text = elem.text.lower() if elem.text else ""
                    elem_name = elem.name.lower() if elem.name else ""
                    elem_id = elem.resource_id.lower() if elem.resource_id else ""
                    if name_lower in elem_text or name_lower in elem_name or name_lower in elem_id:
                        matches.append(elem)
        return matches

    def get_by_text(self, text: str) -> List[MobileElement]:
        """
        Locates elements that contain the specified text.
        """
        matches = []
        text_lower = text.lower()

        for elem in self.get_all_elements():
            elem_text = elem.text.lower() if elem.text else ""
            elem_name = elem.name.lower() if elem.name else ""
            if text_lower in elem_text or text_lower in elem_name:
                matches.append(elem)
        return matches

    def get_by_test_id(self, test_id: str) -> List[MobileElement]:
        """
        Locates elements matching the specified resource ID or iOS equivalent.
        """
        matches = []
        id_lower = test_id.lower()

        for elem in self.get_all_elements():
            elem_id = elem.resource_id.lower() if elem.resource_id else ""
            if id_lower == elem_id or id_lower in elem_id:
                matches.append(elem)
        return matches

    def contains_text(self, needle: str) -> bool:
        """Is this phrase on screen, as a person reading the screen would say?

        Matched against the screen as one running text rather than view by
        view, for the same reason as the web snapshot: a label a tester reads
        as one phrase is very often two views side by side, and checking each
        alone fails an assertion whose subject is plainly on the screen.
        Whitespace inside the phrase is still required — this forgives how the
        layout was split, not what it says.
        """
        needle_norm = " ".join(needle.split()).lower()
        if not needle_norm:
            return False
        parts: List[str] = []
        for elem in self.get_all_elements():
            if not (elem.displayed and elem.visible):
                continue
            for value in (elem.text, elem.name):
                if not value:
                    continue
                normalised = " ".join(value.split())
                if not normalised:
                    continue
                if needle_norm in normalised.lower():
                    return True
                # A label repeated back to back — a view's text and its
                # accessible name usually agree — would otherwise put a phrase
                # in the running text in an order the screen never shows.
                if normalised != (parts[-1] if parts else None):
                    parts.append(normalised)
        return needle_norm in " ".join(parts).lower()

    def visible_text(self) -> List[str]:
        """All visible strings on screen, for assertion failure messages."""
        out = []
        for elem in self.get_all_elements():
            if not (elem.displayed and elem.visible):
                continue
            for value in (elem.text, elem.name):
                if value and value not in out:
                    out.append(value)
        return out
