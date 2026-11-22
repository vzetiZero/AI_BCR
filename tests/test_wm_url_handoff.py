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
    def test_after_lifting_the_tab_the_start_url_becomes_the_wm_url(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = PORTAL
        controller.browser.wm_lifted_url = WM_URL
        controller._remember_wm_url()
        self.assertEqual(controller.config["start_url"], WM_URL,
                         "lần mở Chrome sau phải vào thẳng WM")
        self.assertEqual(controller.config["portal_url"], PORTAL,
                         "phải giữ trang cổng làm đường lùi")

    def test_the_saved_state_contains_the_wm_url(self):
        import json
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = PORTAL
        controller.browser.wm_lifted_url = WM_URL
        controller._remember_wm_url()
        saved = json.loads(controller.state_path.read_text(encoding="utf-8"))["settings"]
        self.assertEqual(saved["start_url"], WM_URL)
        self.assertEqual(saved["portal_url"], PORTAL)

    def test_nothing_is_saved_when_no_url_was_lifted(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = PORTAL
        controller.browser.wm_lifted_url = ""
        controller._remember_wm_url()
        self.assertEqual(controller.config["start_url"], PORTAL)

    def test_the_wm_url_is_recognised_as_wm_and_the_portal_is_not(self):
        self.assertTrue(WT.looks_like_wm(WM_URL))
        self.assertFalse(WT.looks_like_wm(PORTAL))


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
        self.assertEqual(controller.config["start_url"], PORTAL,
                         "sau khi lùi, start_url phải là trang cổng để lấy URL mới")

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

    def test_fallback_is_skipped_when_the_current_url_is_not_wm(self):
        controller = make()
        self.addCleanup(controller.shutdown)
        controller.config["start_url"] = PORTAL
        controller.config["portal_url"] = PORTAL
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
