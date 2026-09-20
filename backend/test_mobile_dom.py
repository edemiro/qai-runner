import unittest
import xml.etree.ElementTree as ET
from mobile_dom import MobileElement, MobileDOMManager

class TestMobileDOM(unittest.TestCase):

    def test_mobile_element_bounds_parsing(self):
        # Test Android bounds format
        raw_xml = '<node bounds="[100,200][900,300]" class="android.widget.Button" text="Click Me"/>'
        node = ET.fromstring(raw_xml)
        elem = MobileElement(node, platform="Android")
        
        self.assertIsNotNone(elem.bounds)
        self.assertEqual(elem.bounds["x1"], 100)
        self.assertEqual(elem.bounds["y1"], 200)
        self.assertEqual(elem.bounds["x2"], 900)
        self.assertEqual(elem.bounds["y2"], 300)
        self.assertEqual(elem.bounds["width"], 800)
        self.assertEqual(elem.bounds["height"], 100)
        self.assertEqual(elem.bounds["cx"], 500)
        self.assertEqual(elem.bounds["cy"], 250)

    def test_mobile_element_ios_reconstruct_bounds(self):
        # Test iOS individual x, y, width, height reconstruction
        raw_xml = '<node x="50" y="80" width="200" height="40" class="XCUIElementTypeButton" label="Submit"/>'
        node = ET.fromstring(raw_xml)
        elem = MobileElement(node, platform="iOS")
        
        self.assertIsNotNone(elem.bounds)
        self.assertEqual(elem.bounds["x1"], 50)
        self.assertEqual(elem.bounds["y1"], 80)
        self.assertEqual(elem.bounds["x2"], 250)
        self.assertEqual(elem.bounds["y2"], 120)
        self.assertEqual(elem.bounds["cx"], 150)
        self.assertEqual(elem.bounds["cy"], 100)

    def test_mobile_element_role_resolution(self):
        # Android
        node1 = ET.fromstring('<node class="android.widget.EditText"/>')
        elem1 = MobileElement(node1, platform="Android")
        self.assertEqual(elem1.role, "textbox")
        
        # iOS
        node2 = ET.fromstring('<node class="XCUIElementTypeButton"/>')
        elem2 = MobileElement(node2, platform="iOS")
        self.assertEqual(elem2.role, "button")
        
        # Fallback with clickable
        node3 = ET.fromstring('<node class="android.widget.FrameLayout" clickable="true"/>')
        elem3 = MobileElement(node3, platform="Android")
        self.assertEqual(elem3.role, "button")

    def test_actionability_viewport(self):
        # Actionable within viewport
        raw_xml = '<node bounds="[10,10][100,100]" displayed="true" visible="true" enabled="true"/>'
        node = ET.fromstring(raw_xml)
        elem = MobileElement(node)
        self.assertTrue(elem.is_actionable(screen_width=1000, screen_height=2000))
        
        # Non-actionable due to hidden state
        raw_xml_hidden = '<node bounds="[10,10][100,100]" displayed="false" visible="true" enabled="true"/>'
        node_hidden = ET.fromstring(raw_xml_hidden)
        elem_hidden = MobileElement(node_hidden)
        self.assertFalse(elem_hidden.is_actionable())

        # Non-actionable due to viewport bounds (outside screen)
        raw_xml_outside = '<node bounds="[1100,200][1200,300]" displayed="true" visible="true" enabled="true"/>'
        node_outside = ET.fromstring(raw_xml_outside)
        elem_outside = MobileElement(node_outside)
        self.assertFalse(elem_outside.is_actionable(screen_width=1080, screen_height=2400))

    def test_tree_optimization_and_flattening(self):
        xml_source = """
        <hierarchy rotation="0">
            <android.widget.FrameLayout bounds="[0,0][1080,2400]" displayed="true">
                <android.widget.LinearLayout bounds="[0,0][1080,2400]" displayed="true">
                    <android.widget.TextView class="android.widget.TextView" text="Hoş Geldiniz" resource-id="com.app:id/titleText" displayed="true" bounds="[100,200][900,300]"/>
                    <android.widget.Button class="android.widget.Button" text="Giriş Yap" resource-id="com.app:id/loginBtn" displayed="true" bounds="[100,500][900,600]"/>
                    <android.widget.TextView class="android.widget.TextView" text="Gizli Hata" displayed="false" bounds="[0,0][0,0]"/>
                </android.widget.LinearLayout>
            </android.widget.FrameLayout>
        </hierarchy>
        """
        
        manager = MobileDOMManager(xml_source, platform="Android", screen_width=1080, screen_height=2400)
        optimized = manager.get_optimized_tree()
        
        # LinearLayout contains 2 vital children (TextView and Button).
        # FrameLayout is a container with only 1 child (LinearLayout).
        # Since FrameLayout has no vital info, and only 1 child, it should be flattened, 
        # meaning LinearLayout is lifted up.
        # However, LinearLayout itself has 2 children, so it won't be flattened.
        
        self.assertIsNotNone(optimized)
        self.assertEqual(optimized["class"], "Linearlayout")
        self.assertEqual(len(optimized["children"]), 2)
        
        # Check text values
        self.assertEqual(optimized["children"][0]["text"], "Hoş Geldiniz")
        self.assertEqual(optimized["children"][0]["id"], "titleText")
        self.assertEqual(optimized["children"][1]["text"], "Giriş Yap")
        self.assertEqual(optimized["children"][1]["id"], "loginBtn")

    def test_semantic_locators(self):
        xml_source = """
        <hierarchy rotation="0">
            <android.widget.FrameLayout bounds="[0,0][1080,2400]" displayed="true">
                <android.widget.LinearLayout bounds="[0,0][1080,2400]" displayed="true">
                    <android.widget.TextView class="android.widget.TextView" text="Kullanıcı Adı" resource-id="com.app:id/usernameLabel" displayed="true" bounds="[100,100][300,150]"/>
                    <android.widget.EditText class="android.widget.EditText" text="" resource-id="com.app:id/usernameInput" displayed="true" bounds="[100,150][900,250]"/>
                    <android.widget.Button class="android.widget.Button" text="Devam Et" content-desc="Submit button" resource-id="com.app:id/submitBtn" displayed="true" bounds="[100,300][900,400]"/>
                </android.widget.LinearLayout>
            </android.widget.FrameLayout>
        </hierarchy>
        """
        manager = MobileDOMManager(xml_source, platform="Android")
        
        # Test get_by_role
        buttons = manager.get_by_role("button")
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].text, "Devam Et")
        
        buttons_named = manager.get_by_role("button", "submit")
        self.assertEqual(len(buttons_named), 1)
        
        # Test get_by_text
        inputs = manager.get_by_role("textbox")
        self.assertEqual(len(inputs), 1)
        
        label = manager.get_by_text("Kullanıcı")
        self.assertEqual(len(label), 1)
        self.assertEqual(label[0].resource_id, "usernameLabel")
        
        # Test get_by_test_id
        submit_btn = manager.get_by_test_id("submitBtn")
        self.assertEqual(len(submit_btn), 1)
        self.assertEqual(submit_btn[0].name, "Submit button")

    def test_semantic_similarity_element_shifts(self):
        from locator import calculate_semantic_similarity
        
        # Define a cached element representing "Pasaj" at its old position
        cached_node = ET.fromstring(
            '<node bounds="[100,500][900,600]" class="android.widget.Button" text="Pasaj" resource-id="com.app:id/tab_pasaj"/>'
        )
        cached_elem = MobileElement(cached_node, platform="Android")
        cached_elem.xpath = "/hierarchy/android.widget.FrameLayout/android.view.ViewGroup[11]"
        
        # Candidate 1: Element at original XPath "/hierarchy/.../ViewGroup[11]" but it is now "TVPlus"
        shifted_wrong_node = ET.fromstring(
            '<node bounds="[100,500][900,600]" class="android.widget.Button" text="TVPlus" resource-id="com.app:id/tab_tvplus"/>'
        )
        candidate_wrong = MobileElement(shifted_wrong_node, platform="Android")
        candidate_wrong.xpath = "/hierarchy/android.widget.FrameLayout/android.view.ViewGroup[11]"
        
        # Candidate 2: "Pasaj" element that shifted to a different index (ViewGroup[8])
        shifted_correct_node = ET.fromstring(
            '<node bounds="[100,300][900,400]" class="android.widget.Button" text="Pasaj" resource-id="com.app:id/tab_pasaj"/>'
        )
        candidate_correct = MobileElement(shifted_correct_node, platform="Android")
        candidate_correct.xpath = "/hierarchy/android.widget.FrameLayout/android.view.ViewGroup[8]"
        
        # Calculate scores
        score_wrong = calculate_semantic_similarity(cached_elem, candidate_wrong)
        score_correct = calculate_semantic_similarity(cached_elem, candidate_correct)
        
        # Verify that the correct shifted element scores much higher
        self.assertGreater(score_correct, score_wrong)
        
        # Verify threshold checks
        self.assertTrue(score_correct >= 35.0, f"Expected correct candidate to pass threshold, got score {score_correct}")
        self.assertTrue(score_wrong < 35.0, f"Expected wrong candidate to fail threshold, got score {score_wrong}")

    def test_semantic_similarity_fallback_xpath(self):
        from locator import calculate_semantic_similarity
        
        # A generic layout container with no text or resource-id
        cached_node = ET.fromstring('<node class="android.view.ViewGroup"/>')
        cached_elem = MobileElement(cached_node, platform="Android")
        cached_elem.xpath = "/hierarchy/android.widget.FrameLayout/android.view.ViewGroup[3]"
        
        # Candidate at same XPath
        candidate_same_xpath = MobileElement(ET.fromstring('<node class="android.view.ViewGroup"/>'), platform="Android")
        candidate_same_xpath.xpath = "/hierarchy/android.widget.FrameLayout/android.view.ViewGroup[3]"
        
        # Candidate at different XPath
        candidate_diff_xpath = MobileElement(ET.fromstring('<node class="android.view.ViewGroup"/>'), platform="Android")
        candidate_diff_xpath.xpath = "/hierarchy/android.widget.FrameLayout/android.view.ViewGroup[4]"
        
        score_same = calculate_semantic_similarity(cached_elem, candidate_same_xpath)
        score_diff = calculate_semantic_similarity(cached_elem, candidate_diff_xpath)
        
        self.assertGreater(score_same, score_diff)
        self.assertTrue(score_same >= 35.0, f"Expected same xpath to pass, got score {score_same}")
        self.assertTrue(score_diff < 35.0, f"Expected different xpath to fail, got score {score_diff}")

if __name__ == "__main__":
    unittest.main()


SCREEN_BEHIND_AN_OVERLAY = """
<hierarchy rotation="0">
  <XCUIElementTypeApplication bounds="[0,0][440,956]" displayed="true" visible="true">
    <XCUIElementTypeStaticText name="Cabin" bounds="[210,272][260,290]"
      displayed="true" visible="false"/>
    <XCUIElementTypeButton name="ECONOMY" bounds="[210,288][270,306]"
      displayed="true" visible="false"/>
    <XCUIElementTypeStaticText name="Off the screen" bounds="[0,4000][100,4020]"
      displayed="true" visible="false"/>
    <XCUIElementTypeButton name="Done" bounds="[0,900][440,940]"
      displayed="true" visible="true"/>
  </XCUIElementTypeApplication>
</hierarchy>
"""


class TextTheDriverSaysIsNotVisible(unittest.TestCase):
    """iOS decides `visible` by hit-testing, so a word under a keyboard
    accessory or a sheet being dismissed comes back hidden while the run's own
    screenshot shows it plainly.

    Measured on the real app: an assertion for "ECONOMY" failed against a
    screen with ECONOMY on it, because the booker sat behind the picker's Done
    bar and every one of its views was marked visible="false".
    """

    def _screen(self):
        return MobileDOMManager(SCREEN_BEHIND_AN_OVERLAY, "iOS", 440, 956)

    def test_the_strict_reading_still_misses_it(self):
        """Unchanged, because a caller asking only for what is hit-testable
        should still get that answer."""
        self.assertFalse(self._screen().contains_text("ECONOMY"))

    def test_position_is_the_second_opinion(self):
        self.assertTrue(self._screen().contains_text("ECONOMY", include_hidden=True))

    def test_the_three_answers_are_told_apart(self):
        screen = self._screen()
        self.assertEqual(screen.find_text("Done"), "visible")
        self.assertEqual(screen.find_text("ECONOMY"), "hidden")
        self.assertIsNone(screen.find_text("Business"))

    def test_something_genuinely_off_the_screen_is_not_rescued(self):
        """Four thousand pixels down is not on the screen by any reading, and
        counting it would make every assertion pass."""
        self.assertIsNone(self._screen().find_text("Off the screen"))
