# -*- coding: utf-8 -*-
"""Yêu cầu: bấm Chạy tool thì tab sảnh phải thành URL WM thật, không đứng ở trang cổng.

Luồng mong muốn:
    https://9vnv.com/vn/wm_casino/?type=fullscreen   (trang cổng, WM trong iframe)
        → bấm Chạy tool
    https://wmvn.m8810.com/?sid=…&ui=7&co=wm&fullscreen=true   (URL cuối cùng)

Và lần mở Chrome sau phải đi thẳng URL WM (không mở lại trang cổng), với đường lùi về
trang cổng nếu token của URL WM đã hết hạn.
"""

import pathlib
import tempfile
import unittest

from aibcr.adapters import wm_targets as WT
from aibcr.controllers.baccarat import BaccaratController

PORTAL = "https://9vnv.com/vn/wm_casino/?type=fullscreen"
WM_URL = ("https://wmvn.m8810.com/?sid=12915I5G50042A168M71478393&ui=7&lang=vi"
          "&co=wm&fullscreen=true")


def make():
    folder = pathlib.Path(tempfile.mkdtemp())
    controller = BaccaratController(
        folder / "s.json", type("E", (), {"event": lambda self, n, d: None})())
    return controller


class RememberWmUrlTests(unittest.TestCase):
    """`start_url` phải là CỔNG của người dùng, không phải URL WM dùng một lần."""

    def test_the_learned_start_url_is_the_portal_root(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = "https://www.neo179.com/gamelobby/live"
        controller.browser.wm_portal_url = "https://miso88-6.com/w/"
        controller.browser.wm_lifted_url = WM_URL
        controller._remember_wm_url()
        self.assertEqual(controller.config["start_url"], "https://miso88-6.com/w/",
                         "lần mở Chrome sau phải vào đúng site người dùng")
        self.assertEqual(controller.config["portal_url"], "https://miso88-6.com/w/")
        self.assertEqual(controller.config["wm_url"], WM_URL,
                         "URL WM chỉ để tham khảo, không làm start_url")

    def test_the_saved_state_contains_the_portal(self):
        import json
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = "https://www.neo179.com/gamelobby/live"
        controller.browser.wm_portal_url = "https://miso88-6.com/w/"
        controller.browser.wm_lifted_url = WM_URL
        controller._remember_wm_url()
        saved = json.loads(controller.state_path.read_text(encoding="utf-8"))["settings"]
        self.assertEqual(saved["start_url"], "https://miso88-6.com/w/")
        self.assertEqual(saved["portal_url"], "https://miso88-6.com/w/")
        self.assertEqual(saved["wm_url"], WM_URL)

    def test_a_user_chosen_start_url_is_never_overwritten(self):
        """Người dùng có thể dùng mirror khác với ``returnurl`` của link WM."""
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.dispatch("config.set", {"start_url": "https://miso88-6.com/w/"})
        controller.browser.wm_portal_url = "https://miso88-7.com/"
        controller.browser.wm_lifted_url = WM_URL
        controller._remember_wm_url()
        self.assertEqual(controller.config["start_url"], "https://miso88-6.com/w/")
        self.assertTrue(controller.config["start_url_locked"])

    def test_nothing_is_saved_when_no_url_was_lifted(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = PORTAL
        controller.browser.wm_lifted_url = ""
        controller.browser.wm_portal_url = ""
        controller.browser._cdp_pages = lambda: []
        controller._remember_wm_url()
        self.assertEqual(controller.config["start_url"], PORTAL)

    def test_the_wm_url_is_recognised_as_wm_and_the_portal_is_not(self):
        self.assertTrue(WT.looks_like_wm(WM_URL))
        self.assertFalse(WT.looks_like_wm(PORTAL))

    def test_portal_root_keeps_only_the_first_path_segment(self):
        from aibcr.adapters import browser as BR

        self.assertEqual(BR.portal_root("https://miso88-7.com/w/game-lobby/casino"),
                         "https://miso88-7.com/w/")
        self.assertEqual(BR.portal_root("https://miso88-7.com/"),
                         "https://miso88-7.com/")
        self.assertEqual(BR.portal_root("https://9vnv.com/vn/wm_casino/?type=fullscreen"),
                         "https://9vnv.com/vn/")

    def test_portal_is_read_from_the_returnurl_parameter(self):
        from aibcr.adapters import browser as BR

        url = ("https://wmvn.m8810.com/?sid=62715A9G&ui=7&lang=vi"
               "&returnurl=https://miso88-7.com&co=wm&fullscreen=true")
        self.assertEqual(BR.portal_from_wm_url(url), "https://miso88-7.com")
        self.assertEqual(BR.portal_root(BR.portal_from_wm_url(url)),
                         "https://miso88-7.com/")


class PortalFallbackTests(unittest.TestCase):
    def test_a_dead_wm_url_falls_back_to_the_portal(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = WM_URL
        controller.config["portal_url"] = PORTAL
        navigated = []
        controller.browser.navigate_current = (
            lambda url, wait_seconds=15.0: navigated.append(url) or (True, "ok"))
        self.assertTrue(controller._fallback_to_portal("hết hạn"))
        self.assertEqual(navigated, [PORTAL])

    def test_fallback_does_not_loop(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = WM_URL
        controller.config["portal_url"] = PORTAL
        calls = []
        controller.browser.navigate_current = (
            lambda url, wait_seconds=15.0: calls.append(url) or (True, "ok"))
        self.assertTrue(controller._fallback_to_portal("lần 1"))
        self.assertFalse(controller._fallback_to_portal("lần 2"),
                         "không được nhảy về cổng liên tục")
        self.assertEqual(len(calls), 1)

    def test_fallback_is_skipped_without_a_saved_portal(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = WM_URL
        controller.config["portal_url"] = ""
        calls = []
        controller.browser.navigate_current = (
            lambda url, wait_seconds=15.0: calls.append(url) or (True, "ok"))
        self.assertFalse(controller._fallback_to_portal("x"))
        self.assertEqual(calls, [])


class TypedStartUrlTests(unittest.TestCase):
    def test_typing_a_portal_url_records_it_as_the_fallback(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        reply = controller.dispatch("config.set", {"start_url": PORTAL})[0]
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(controller.config["start_url"], PORTAL)
        self.assertEqual(controller.config["portal_url"], PORTAL)

    def test_typing_a_wm_url_keeps_the_previous_portal(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["portal_url"] = PORTAL
        reply = controller.dispatch("config.set", {"start_url": WM_URL})[0]
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(controller.config["start_url"], WM_URL)
        self.assertEqual(controller.config["portal_url"], PORTAL,
                         "portal cũ vẫn phải còn để lùi khi token hết hạn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
