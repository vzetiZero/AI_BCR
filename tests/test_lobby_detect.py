# -*- coding: utf-8 -*-
"""Kiểm thử nhận diện tab sảnh: URL cổng thực tế + WM_DETECT_JS có dự phòng.

Vì sao cần: người dùng dùng cổng khác (vd ``https://m.9vnv.com/vn/wm_casino?from=casino``)
và bản mobile có thể không đặt iframe ``iframe_109/iframe_101``. Nếu chỉ dựa vào id
iframe thì app báo "Chưa thấy tab sảnh Baccarat" dù sảnh đang mở ngay đó.
"""

import unittest

from aibcr.adapters import browser as BR
from aibcr.adapters import wm


class PortalUrlTests(unittest.TestCase):
    def test_any_public_https_portal_is_allowed(self):
        for url in ("https://m.9vnv.com/vn/wm_casino?from=casino",
                    "https://m.9vnv.com/vn/wm_casino",
                    "https://www.neo179.com/gamelobby/live"):
            with self.subTest(url=url):
                self.assertTrue(BR.valid_site_url(url), url)

    def test_casino_shell_is_not_a_lobby_url(self):
        """Trang casino bọc ngoài KHÔNG phải sảnh: phải vào sảnh bên trong.

        Đây không phải hạn chế mà là thiết kế: tab bọc ngoài không chứa danh sách bàn,
        nên nhận nó là sảnh sẽ khiến app quét mãi không thấy bàn.
        """
        url = "https://m.9vnv.com/vn/wm_casino?from=casino"
        self.assertFalse(BR.is_lobby_url(url))
        self.assertFalse(BR.is_petalive_table_url(url))

    def test_entry_and_local_urls_are_still_rejected(self):
        self.assertFalse(BR.valid_site_url("https://example.com/entry"))
        self.assertFalse(BR.valid_site_url("http://m.9vnv.com/vn/wm_casino"))
        self.assertFalse(BR.valid_site_url("https://127.0.0.1/vn/wm_casino"))
        self.assertFalse(BR.valid_site_url("https://localhost/vn/wm_casino"))


class WmDetectFallbackTests(unittest.TestCase):
    """WM_DETECT_JS phải nhận sảnh theo NỘI DUNG khi iframe id đổi (mirror/mobile)."""

    def test_keeps_iframe_id_detection(self):
        js = wm.WM_DETECT_JS
        self.assertIn("iframe_(109|101)", js)
        self.assertIn("frames['109']", js)
        self.assertIn("frames['101']", js)

    def test_falls_back_to_lobby_dom_markers(self):
        js = wm.WM_DETECT_JS
        self.assertIn("game_list_box", js)
        self.assertIn("groupBac_", js)
        self.assertIn("section.game_list.roomItem", js)

    def test_falls_back_to_table_dom_markers(self):
        js = wm.WM_DETECT_JS
        self.assertIn("bigRoadContainer", js)
        self.assertIn("userBalanceBox", js)
        self.assertIn("game\\d+", js)

    def test_reports_evidence_so_the_user_knows_what_was_seen(self):
        js = wm.WM_DETECT_JS
        self.assertIn("evidence", js)
        self.assertIn("evidence.push", js)
        self.assertIn("title:", js)

    def test_lobby_wins_over_table_when_both_markers_exist(self):
        """Cả hai dấu hiệu cùng có: ưu tiên sảnh, vì đang quét danh sách bàn."""
        js = wm.WM_DETECT_JS
        self.assertIn("contentTable && !contentLobby", js)

    def test_detection_never_throws_on_cross_origin_frames(self):
        js = wm.WM_DETECT_JS
        self.assertIn("try { doc = frame.contentDocument; } catch (e)", js)
        self.assertIn("probe_error", js)


class DiagnoseTests(unittest.TestCase):
    def test_diagnose_requires_cdp(self):
        from aibcr.controllers.baccarat import BaccaratController
        import pathlib
        import tempfile

        folder = pathlib.Path(tempfile.mkdtemp())
        controller = BaccaratController(
            folder / "state.json", type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        reply = controller.lobby_diagnose()
        self.assertFalse(reply["ok"])
        self.assertIn("CDP", reply["error"])

    def test_probe_tabs_classifies_each_kind(self):
        from aibcr.adapters.lobby_stream import LobbyStream

        class FakeSession:
            def _cdp_pages(self):
                return [
                    {"url": "https://m.9vnv.com/vn/wm_casino?from=casino"},
                    {"url": "https://mirror.example.com/frontend/evo/r3#x"},
                    {"url": "https://mirror.example.com/entry"},
                ]

        stream = LobbyStream(FakeSession(), game="baccarat")
        stream._probe_wm = lambda page: (
            {"is_wm": True, "kind": "lobby", "evidence": ["game_list_box"]}
            if "wm_casino" in page.get("url", "") else {})
        reports = stream.probe_tabs()
        kinds = {row["url"]: row["kind"] for row in reports}
        self.assertEqual(kinds["https://m.9vnv.com/vn/wm_casino"], "wm-lobby")
        self.assertEqual(kinds["https://mirror.example.com/frontend/evo/r3"], "evo")
        self.assertEqual(kinds["https://mirror.example.com/entry"], "entry")

    def test_probe_tabs_marks_plain_portal_as_unknown(self):
        from aibcr.adapters.lobby_stream import LobbyStream

        class FakeSession:
            def _cdp_pages(self):
                return [{"url": "https://www.neo179.com/gamelobby/live"}]

        stream = LobbyStream(FakeSession(), game="baccarat")
        stream._probe_wm = lambda page: {}
        # Cô lập: không được chạm CDP thật (máy dev có thể đang mở Chrome 9222).
        stream._probe_oopif = lambda: None
        reports = stream.probe_tabs()
        self.assertEqual(reports[0]["kind"], "unknown",
                         "trang cổng không phải sảnh — phải nói rõ để người dùng vào sảnh")


if __name__ == "__main__":
    unittest.main(verbosity=2)
