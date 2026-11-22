# -*- coding: utf-8 -*-
"""Kiểm thử WM nằm trong iframe KHÁC ORIGIN (OOPIF).

Tình huống thật: cổng `https://m.9vnv.com/vn/wm_casino?from=casino` nhúng WM bằng
iframe `https://wmvn.m8810.com/?sid=…&co=wm&fullscreen=true`. Vì khác origin, Chrome:
  · chặn `iframe.contentDocument` → dò DOM khung gốc không thấy WM;
  · tách iframe thành target riêng (OOPIF) → `Network.enable` ở tab mẹ không thấy
    WebSocket của WM.
Vì vậy phải dò ở cấp target và gắn vào target iframe. Các test dưới khoá hành vi đó.
"""

import pathlib
import sys
import types
import unittest

from aibcr.adapters import wm_targets as WT

WRAPPER = "https://m.9vnv.com/vn/wm_casino?from=casino"
WM_FRAME = "https://wmvn.m8810.com/?sid=12993815C4C50012A168M71476&ui=7&lang=vi&co=wm&fullscreen=true"


class LooksLikeWmTests(unittest.TestCase):
    def test_the_real_wm_frame_url_is_recognised(self):
        self.assertTrue(WT.looks_like_wm(WM_FRAME))

    def test_the_wrapper_casino_page_is_not_wm(self):
        self.assertFalse(WT.looks_like_wm(WRAPPER))

    def test_other_lobbies_are_not_mistaken_for_wm(self):
        for url in ("https://mirror.example.com/frontend/evo/r3",
                    "https://example.com/live-games/lobby/bcr",
                    "https://example.com/entry"):
            with self.subTest(url=url):
                self.assertFalse(WT.looks_like_wm(url))

    def test_a_bare_sid_query_alone_is_not_enough(self):
        """``sid=`` xuất hiện ở nhiều site; chỉ nhận khi có ``co=wm`` hoặc host WM."""
        self.assertFalse(WT.looks_like_wm("https://shop.example.com/?sid=99"))
        self.assertTrue(WT.looks_like_wm("https://game.example.com/?sid=99&co=wm"))


class PickTargetTests(unittest.TestCase):
    def targets(self):
        return [
            {"id": "P1", "type": "page", "url": WRAPPER},
            {"id": "F1", "type": "iframe", "url": WM_FRAME, "parentId": "P1"},
            {"id": "F2", "type": "iframe", "url": "https://ads.example.com/banner"},
        ]

    def test_prefers_the_wm_iframe(self):
        picked = WT.pick_wm_target(self.targets())
        self.assertIsNotNone(picked)
        self.assertEqual(picked["id"], "F1")

    def test_falls_back_to_a_wm_page(self):
        targets = [{"id": "P9", "type": "page", "url": WM_FRAME}]
        self.assertEqual(WT.pick_wm_target(targets)["id"], "P9")

    def test_returns_none_when_nothing_matches(self):
        self.assertIsNone(WT.pick_wm_target([{"id": "P1", "type": "page", "url": WRAPPER}]))

    def test_find_wm_frame_target_resolves_the_parent_tab(self):
        expected = self.targets()

        def fake_all_targets(port=9222, timeout=4.0):
            return expected

        original = WT.all_targets
        WT.all_targets = fake_all_targets
        try:
            frame, parent = WT.find_wm_frame_target()
        finally:
            WT.all_targets = original
        self.assertEqual(frame["id"], "F1")
        self.assertEqual(parent["id"], "P1")

    def test_describe_exposes_the_wrapper_and_the_frame(self):
        rows = WT.describe(self.targets())
        by_url = {row["url"]: row for row in rows}
        self.assertFalse(by_url[WRAPPER]["wm"], "trang bọc không phải WM")
        self.assertTrue(by_url[WM_FRAME]["wm"], "iframe WM phải được đánh dấu")


class FrameSocketTests(unittest.TestCase):
    """FrameSocket phải gắn sessionId vào từng lệnh và lọc message của target khác."""

    def make(self, queue=None):
        """Fake transport: trả lời theo id của lệnh vừa gửi, hoặc theo hàng đợi cho sẵn."""
        import json

        sent = []
        pending = list(queue or [])

        class FakeSocket:
            def __init__(self):
                self.last = None
                self.queue = list(pending)

            def send(self, payload):
                sent.append(payload)
                self.last = json.loads(payload)

            def recv(self):
                if self.queue:
                    return json.dumps(self.queue.pop(0))
                request_id = (self.last or {}).get("id", 0)
                method = (self.last or {}).get("method", "")
                if method == "Runtime.evaluate":
                    result = {"result": {"value": {"is_wm": True}}}
                else:
                    result = {}
                return json.dumps({"id": request_id, "sessionId": "S1",
                                   "result": result})

            def settimeout(self, value):
                pass

            def close(self):
                pass

        socket = WT.FrameSocket.__new__(WT.FrameSocket)
        socket.port = 9222
        socket.target_id = "F1"
        socket.session_id = "S1"
        socket._seq = 0
        socket._timeout = 5
        socket._socket = FakeSocket()
        socket._browser_call = lambda method, params=None: {"sessionId": "S1"}
        return socket, sent

    def test_send_attaches_the_session_id(self):
        socket, sent = self.make()
        socket.send('{"id": 7, "method": "Network.enable"}')
        import json
        payload = json.loads(sent[-1])
        self.assertEqual(payload["sessionId"], "S1")
        self.assertEqual(payload["id"], 7)

    def test_recv_skips_browser_and_foreign_messages(self):
        socket, _sent = self.make(queue=[
            # Sự kiện cấp trình duyệt: không có sessionId → bỏ qua.
            {"method": "Target.targetCreated"},
            # Của tab khác: phải bỏ qua.
            {"sessionId": "other", "method": "Network.webSocketFrameReceived"},
            # Phản hồi của chính mình.
            {"id": 1, "sessionId": "S1", "result": {"sessionId": "S1"}},
        ])
        import json
        message = json.loads(socket.recv())
        self.assertEqual(message["id"], 1, "phải bỏ qua message không thuộc session")
        self.assertNotIn("sessionId", message, "sessionId không được rò ra ngoài")

    def test_evaluate_returns_the_value_from_the_frame(self):
        socket, sent = self.make()
        value = socket.evaluate("1")
        self.assertEqual(value, {"is_wm": True})
        import json
        # evaluate phải gửi lệnh Runtime.evaluate qua send (nên có sessionId).
        sent_message = json.loads(sent[-1])
        self.assertEqual(sent_message["method"], "Runtime.evaluate")
        self.assertEqual(sent_message["sessionId"], "S1")


class AdoptOopifTests(unittest.TestCase):
    """`BrowserSession._adopt_oopif_wm` lưu target iframe để luồng đọc dùng."""

    def session(self):
        from aibcr.adapters import browser as BR
        return BR.BrowserSession()

    def test_adopts_the_frame_and_records_parent(self):
        session = self.session()
        frame = {"id": "F1", "type": "iframe", "url": WM_FRAME, "parentId": "P1"}
        parent = {"id": "P1", "type": "page", "url": WRAPPER}

        original_find = WT.find_wm_frame_target
        original_open = WT.open_frame_socket
        WT.find_wm_frame_target = lambda port=9222: (frame, parent)

        class FakeFrameSocket:
            def command(self, method, params=None):
                return {}

            def evaluate(self, expression):
                return {"is_wm": True, "kind": "lobby", "evidence": ["game_list_box"]}

            def close(self):
                pass

        WT.open_frame_socket = lambda port=9222, target_id=None, timeout=10.0: FakeFrameSocket()
        try:
            adopted = session._adopt_oopif_wm()
        finally:
            WT.find_wm_frame_target = original_find
            WT.open_frame_socket = original_open
        self.assertTrue(adopted)
        self.assertEqual(session.wm_oopif_target, "F1")
        self.assertEqual(session.wm_oopif_parent, "P1")
        self.assertEqual(session.wm_page_id, "P1")
        self.assertTrue(session.wm_oopif_detect.get("is_wm"))
        session.stop_wm_watch()

    def test_does_not_adopt_when_the_frame_is_not_wm(self):
        session = self.session()
        frame = {"id": "F2", "type": "iframe", "url": "https://ads.example.com/x"}
        original_find = WT.find_wm_frame_target
        original_open = WT.open_frame_socket
        WT.find_wm_frame_target = lambda port=9222: (frame, None)

        class FakeFrameSocket:
            def command(self, method, params=None):
                return {}

            def evaluate(self, expression):
                return {"is_wm": False}

            def close(self):
                pass

        WT.open_frame_socket = lambda port=9222, target_id=None, timeout=10.0: FakeFrameSocket()
        try:
            adopted = session._adopt_oopif_wm()
        finally:
            WT.find_wm_frame_target = original_find
            WT.open_frame_socket = original_open
        self.assertFalse(adopted)
        self.assertEqual(session.wm_oopif_target, "")
        session.stop_wm_watch()

    def test_probe_wm_pages_uses_the_oopif_path_first(self):
        session = self.session()
        calls = []
        session._adopt_oopif_wm = lambda: calls.append(True) or True
        self.assertTrue(session._probe_wm_pages())
        self.assertEqual(calls, [True])


class ProbeTabsWrapperTests(unittest.TestCase):
    def test_wrapper_tab_and_oopif_are_both_reported(self):
        from aibcr.adapters import browser as BR
        from aibcr.adapters.lobby_stream import LobbyStream

        class FakeSession(BR.BrowserSession):
            def _cdp_pages(self):
                return [{"id": "P1", "url": WRAPPER}]

        stream = LobbyStream(FakeSession(), game="baccarat")
        stream._probe_wm = lambda page: {}
        stream._probe_oopif = lambda: {
            "url": "https://wmvn.m8810.com/", "kind": "wm-lobby",
            "evidence": ["game_list_box", "iframe khác origin"], "title": "WM",
            "parent": WRAPPER,
        }
        reports = stream.probe_tabs()
        kinds = [row["kind"] for row in reports]
        self.assertIn("wrapper", kinds, "tab mẹ phải nói rõ chỉ là trang bọc")
        self.assertIn("wm-lobby", kinds, "iframe WM phải xuất hiện như một mục riêng")


class SweepUsesOopifTests(unittest.TestCase):
    """`sweep()` phải mở socket tới TARGET iframe, không phải tab mẹ."""

    def test_sweep_opens_the_frame_socket_when_oopif_is_known(self):
        from aibcr.adapters import browser as BR
        from aibcr.adapters.lobby_stream import LobbyStream

        session = BR.BrowserSession()
        session.wm_oopif_target = "F1"
        stream = LobbyStream(session, game="baccarat")

        used = {}

        class FakeFrameSocket:
            def __init__(self):
                self._last = {}

            def command(self, method, params=None):
                used.setdefault("commands", []).append(method)
                return {"result": {"value": {"game": 101, "group": 7}}}

            def evaluate(self, expression):
                return {"is_wm": True, "kind": "lobby"}

            def send(self, payload):
                import json
                self._last = json.loads(payload)

            def recv(self):
                # Phải trả đúng id của lệnh vừa gửi, nếu không `_command` chờ mãi.
                import json
                return json.dumps({"id": (self._last or {}).get("id", 0), "result": {}})

            def settimeout(self, value):
                pass

            def close(self):
                pass

        def fake_open(port=9222, target_id=None, timeout=10.0):
            used["target_id"] = target_id
            return FakeFrameSocket()

        original = WT.open_frame_socket
        # Target phải "còn sống" thì luồng mới đi qua iframe (đây là guard mới).
        original_alive = LobbyStream._target_alive
        WT.open_frame_socket = fake_open
        LobbyStream._target_alive = staticmethod(lambda target_id: True)
        try:
            stream._ensure_wm_page = lambda: True
            stream._wait_lobby_context = lambda ws, seconds=25.0: (1, {"tiles": []})
            stream._wm_detect = lambda ws: {"is_wm": True, "kind": "lobby"}
            stream._sweep_wm = lambda ws, ctx, snap, mx, fav: [{"table_id": "101_7"}]
            rows = stream._sweep_through_oopif(max_seconds=1, favorites_only=False)
        finally:
            WT.open_frame_socket = original
            LobbyStream._target_alive = original_alive
        self.assertEqual(used.get("target_id"), "F1",
                         "phải gắn đúng target iframe, không phải tab mẹ")
        self.assertEqual(rows, [{"table_id": "101_7"}])
        session.stop_wm_watch()

    def test_a_dead_oopif_target_falls_back_instead_of_failing(self):
        """iframe reload/đóng làm target chết: phải dò lại, không bám socket hỏng.

        Đây là nguyên nhân "lúc quét được lúc không" khi người dùng đứng ở trang cổng.
        """
        from aibcr.adapters import browser as BR
        from aibcr.adapters.lobby_stream import LobbyStream

        session = BR.BrowserSession()
        session.wm_oopif_target = "DEAD"
        session.wm_oopif_parent = "P1"
        stream = LobbyStream(session, game="baccarat")
        opened = []

        original_open = WT.open_frame_socket
        WT.open_frame_socket = lambda port=9222, target_id=None, timeout=10.0: (
            opened.append(target_id) or (_ for _ in ()).throw(RuntimeError("gone")))
        try:
            result = stream._sweep_through_oopif(max_seconds=1, favorites_only=False)
        finally:
            WT.open_frame_socket = original_open
        self.assertIsNone(result, "phải trả None để lớp gọi fallback sang tab trực tiếp")
        self.assertEqual(session.wm_oopif_target, "", "cache chết phải bị xoá")
        session.stop_wm_watch()

    def test_target_alive_reports_missing_ids(self):
        from aibcr.adapters.lobby_stream import LobbyStream

        original = WT.all_targets
        WT.all_targets = lambda port=9222, timeout=4.0: [{"id": "A1"}, {"id": "A2"}]
        try:
            self.assertTrue(LobbyStream._target_alive("A1"))
            self.assertFalse(LobbyStream._target_alive("DEAD"))
        finally:
            WT.all_targets = original

    def test_target_alive_is_false_when_cdp_is_down(self):
        from aibcr.adapters.lobby_stream import LobbyStream

        original = WT.all_targets

        def boom(port=9222, timeout=4.0):
            raise OSError("cdp down")

        WT.all_targets = boom
        try:
            self.assertFalse(LobbyStream._target_alive("A1"))
        finally:
            WT.all_targets = original


class EnsureWmPageOrderTests(unittest.TestCase):
    """Ưu tiên tab WM trực tiếp trước iframe (ổn định hơn)."""

    def test_direct_tab_is_preferred_over_oopif(self):
        from aibcr.adapters import browser as BR
        from aibcr.adapters.lobby_stream import LobbyStream

        session = BR.BrowserSession()

        class FakeSession(BR.BrowserSession):
            def _cdp_pages(self):
                return [{"id": "DIRECT", "url": WRAPPER},
                        {"id": "P2", "url": "https://wmvn.m8810.com/?sid=x&co=wm"}]

        stream = LobbyStream(FakeSession(), game="baccarat")
        used = []
        stream._probe_wm = lambda page: (
            {"is_wm": True, "kind": "lobby"} if page.get("id") == "P2" else {})
        FakeSession._adopt_oopif_wm = lambda self: used.append("oopif") or True
        stream._wm_probe_at = 0.0
        self.assertTrue(stream._ensure_wm_page())
        self.assertEqual(used, [], "có tab WM trực tiếp thì không cần iframe")
        self.assertEqual(stream.session.wm_page_id, "P2")

    def test_oopif_is_used_when_only_the_wrapper_exists(self):
        from aibcr.adapters import browser as BR
        from aibcr.adapters.lobby_stream import LobbyStream

        class OnlyWrapper(BR.BrowserSession):
            def _cdp_pages(self):
                return [{"id": "P1", "url": WRAPPER}]

        session = OnlyWrapper()
        stream = LobbyStream(session, game="baccarat")
        stream._probe_wm = lambda page: {}
        OnlyWrapper._adopt_oopif_wm = lambda self: True
        stream._wm_probe_at = 0.0
        self.assertTrue(stream._ensure_wm_page())
        self.assertEqual(stream.lobby_kind, "wm")
        session.stop_wm_watch()

    def test_the_scan_can_run_from_the_wrapper_without_copying_the_iframe_url(self):
        """Yêu cầu người dùng: đứng ở trang cổng là quét được, không phải copy link."""
        from aibcr.adapters import browser as BR
        from aibcr.adapters.lobby_stream import LobbyStream

        class WrapperOnly(BR.BrowserSession):
            def _cdp_pages(self):
                return [{"id": "P1", "url": WRAPPER}]

        session = WrapperOnly()
        stream = LobbyStream(session, game="baccarat")
        stream._probe_wm = lambda page: {}
        WrapperOnly._adopt_oopif_wm = lambda self: True
        stream._wm_probe_at = 0.0
        stream._sweep_through_oopif = lambda mx, fav: [{"table_id": "101_7"}]
        rows = stream.sweep(max_seconds=1)
        self.assertEqual(rows, [{"table_id": "101_7"}])
        session.stop_wm_watch()


if __name__ == "__main__":
    unittest.main(verbosity=2)
