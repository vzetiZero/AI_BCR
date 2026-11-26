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


class CdpAvailableTests(unittest.TestCase):
    """`_cdp_available` không được đòi driver Selenium.

    Lỗi thật: người dùng đứng ở `9vnv.com/vn/wm_casino` (WM trong iframe khác origin).
    Đường OOPIF không dùng driver, nhưng `_cdp_available` cũ trả False khi
    `browser.driver is None` → nút “VÀO BÀN HỢP LỆ” và quét sảnh bị chặn oan dù
    chẩn đoán đã báo “Sảnh WM — dùng được”.
    """

    def test_available_without_driver_when_port_is_open(self):
        import urllib.request
        import pathlib
        import tempfile

        from aibcr.controllers.baccarat import BaccaratController

        folder = pathlib.Path(tempfile.mkdtemp())
        controller = BaccaratController(
            folder / "s.json", type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.browser.driver = None
        controller.browser.cdp_attached = False
        controller._cdp_probe = (False, 0.0)

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *args, **kwargs: FakeResponse()
        try:
            self.assertTrue(controller._cdp_available(),
                            "port mở là đủ cho đường OOPIF, không cần driver")
        finally:
            urllib.request.urlopen = original

    def test_unavailable_when_port_is_closed(self):
        import urllib.request
        import pathlib
        import tempfile

        from aibcr.controllers.baccarat import BaccaratController

        folder = pathlib.Path(tempfile.mkdtemp())
        controller = BaccaratController(
            folder / "s.json", type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.browser.driver = None
        controller.browser.cdp_attached = False
        controller._cdp_probe = (False, 0.0)

        def refuse(*args, **kwargs):
            raise ConnectionRefusedError(10061, "refused")

        original = urllib.request.urlopen
        urllib.request.urlopen = refuse
        try:
            self.assertFalse(controller._cdp_available())
        finally:
            urllib.request.urlopen = original

    def test_attached_flag_short_circuits(self):
        import pathlib
        import tempfile

        from aibcr.controllers.baccarat import BaccaratController

        folder = pathlib.Path(tempfile.mkdtemp())
        controller = BaccaratController(
            folder / "s.json", type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.browser.cdp_attached = True
        self.assertTrue(controller._cdp_available())

    def test_result_is_cached_so_polling_does_not_stall(self):
        """``snapshot()`` gọi hàm này mỗi nhịp poll: phải cache, không dò lại liên tục.

        Lỗi thật: khi Chrome đóng, mỗi lần UI poll bị chặn trọn 1,5 giây (timeout dò
        cổng) nên cửa sổ đứng hình dù không có gì để làm.
        """
        import pathlib
        import tempfile
        import time

        from aibcr.controllers.baccarat import BaccaratController

        folder = pathlib.Path(tempfile.mkdtemp())
        controller = BaccaratController(
            folder / "s.json", type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.browser.driver = None
        controller.browser.cdp_attached = False
        calls = []

        def probe():
            calls.append(time.time())
            return True

        controller._probe_cdp_port = probe
        self.assertTrue(controller._cdp_available())
        for _ in range(20):
            self.assertTrue(controller._cdp_available())
        self.assertEqual(len(calls), 1, "20 nhịp poll chỉ được dò cổng một lần")

    def test_probe_timeout_is_short(self):
        """Cổng đóng phải trả lời nhanh: timeout dò không được lớn như timeout thao tác."""
        from aibcr.controllers import baccarat as BA

        self.assertLessEqual(BA.CDP_PROBE_TIMEOUT, 0.5)


class LiftIframeToTabTests(unittest.TestCase):
    """Mở URL của iframe WM thành tab thật: bỏ hẳn nhánh iframe phức tạp.

    Yêu cầu người dùng: đứng ở trang cổng (9vnv) vẫn dùng được; thay vì bắt app xử lý
    iframe khác origin ở mọi thao tác, lấy chính URL trong iframe (wmvn.m8810.com/?sid=…)
    và mở nó ngay trong tab đó. Sau bước này tab là WM "thật" nên đọc ví, đọc cầu và bấm
    chip chạy nguyên logic cũ.
    """

    def test_navigates_the_wrapper_tab_to_the_iframe_url(self):
        from aibcr.adapters import browser as BR

        session = BR.BrowserSession()
        frame = {"id": "F1", "type": "iframe", "url": WM_FRAME, "parentId": "P1"}
        parent = {"id": "P1", "type": "page", "url": WRAPPER,
                  "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/P1"}
        navigated = []

        original_find = WT.find_wm_frame_target
        WT.find_wm_frame_target = lambda port=9222: (frame, parent)
        session._cdp_pages = lambda: [parent]
        session._probe_wm_page = lambda page: {"is_wm": True, "kind": "lobby"}

        class FakeWs:
            def __init__(self):
                self.sent = []

            def send(self, payload):
                import json
                message = json.loads(payload)
                self.sent.append(message)
                if message.get("method") == "Page.navigate":
                    navigated.append(message["params"]["url"])

            def recv(self):
                import json
                return json.dumps({"id": self.sent[-1]["id"], "result": {}})

            def settimeout(self, value):
                pass

            def close(self):
                pass

        original_conn = __import__("websocket").create_connection
        __import__("websocket").create_connection = lambda *a, **k: FakeWs()
        try:
            ok, note = session.lift_wm_iframe_to_tab(wait_seconds=1)
        finally:
            WT.find_wm_frame_target = original_find
            __import__("websocket").create_connection = original_conn
        self.assertTrue(ok, note)
        self.assertEqual(navigated, [WM_FRAME], "phải mở đúng URL trong iframe")
        self.assertEqual(session.wm_page_id, "P1")
        self.assertEqual(session.wm_oopif_target, "", "không còn cần target iframe")
        session.stop_wm_watch()

    def test_rejects_a_non_wm_iframe_url(self):
        from aibcr.adapters import browser as BR

        session = BR.BrowserSession()
        frame = {"id": "F2", "type": "iframe", "url": "https://ads.example.com/x"}
        original = WT.find_wm_frame_target
        WT.find_wm_frame_target = lambda port=9222: (frame, None)
        try:
            ok, note = session.lift_wm_iframe_to_tab(wait_seconds=1)
        finally:
            WT.find_wm_frame_target = original
        self.assertFalse(ok)
        self.assertIn("WM", note)

    def test_ensure_wm_page_prefers_lifting_over_oopif(self):
        from aibcr.adapters import browser as BR
        from aibcr.adapters.lobby_stream import LobbyStream

        class WrapperOnly(BR.BrowserSession):
            def _cdp_pages(self):
                return [{"id": "P1", "url": WRAPPER}]

        session = WrapperOnly()
        stream = LobbyStream(session, game="baccarat")
        stream._probe_wm = lambda page: {}
        order = []
        WrapperOnly.lift_wm_iframe_to_tab = lambda self, wait_seconds=20.0: (
            order.append("lift") or (True, "ok"))
        WrapperOnly._adopt_oopif_wm = lambda self: order.append("oopif") or True
        stream._wm_probe_at = 0.0
        self.assertTrue(stream._ensure_wm_page())
        self.assertEqual(order, ["lift"], "phải ưu tiên đưa iframe ra tab thật")
        session.stop_wm_watch()


class WalletReadTests(unittest.TestCase):
    """Số dư ví nằm trong `#userBalanceBox` của iframe game — phải đọc được."""

    def test_wallet_js_reads_the_balance_box(self):
        from aibcr.adapters import wm as WM

        js = WM.WM_WALLET_JS
        self.assertIn("userBalanceBox", js)
        self.assertIn("VNDK", js)
        self.assertIn("iframe_101", js)
        self.assertIn("iframe_109", js)

    def test_read_wm_wallet_parses_vndk_text(self):
        from aibcr.adapters import browser as BR

        session = BR.BrowserSession()
        session.wm_socket = None
        session._read_wm_wallet_once = lambda timeout=6.0: {
            "text": "VNDK 1,994.74", "value": "1,994.74"}
        result = session.read_wm_wallet()
        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(result["value"], 1994.74, places=2)

    def test_read_wm_wallet_reports_missing_box(self):
        from aibcr.adapters import browser as BR

        session = BR.BrowserSession()
        session.wm_socket = None
        session._read_wm_wallet_once = lambda timeout=6.0: {"reason": "no_wallet"}
        result = session.read_wm_wallet()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_wallet")


class DetectLayoutTests(unittest.TestCase):
    """Layout một-document (không iframe_101/109) phải nhận đúng sảnh/bàn.

    Lỗi thật: khi vào bàn, ``#game_list_box`` vẫn tồn tại trong DOM nhưng bị đặt
    ``visibility: hidden``, còn ``#container``/``#bigRoadContainer`` không có. Detector
    cũ chỉ kiểm tra SỰ TỒN TẠI nên luôn trả ``lobby`` → app không bao giờ thấy "đã vào
    bàn" và cứ chờ mãi.
    """

    def test_the_detector_checks_visibility_not_just_existence(self):
        from aibcr.adapters import wm as WM

        js = WM.WM_DETECT_JS
        self.assertIn("const shown = (el)", js)
        self.assertIn("visibility !== 'hidden'", js)
        self.assertIn("shown(document.getElementById('game_list_box'))", js)

    def test_table_is_detected_without_any_iframe(self):
        from aibcr.adapters import wm as WM

        js = WM.WM_DETECT_JS
        # Nút thoát bàn + ô số dư là dấu hiệu bàn khi không có iframe/container.
        self.assertIn("shown(document.getElementById('exit_game_btn'))", js)
        self.assertIn("shown(document.getElementById('userBalanceBox'))", js)

    def test_evidence_is_declared_before_it_is_used(self):
        """Trước đây khối "nhìn sang document cha" đẩy vào ``evidence`` khi biến còn ở
        vùng chết (TDZ) → ReferenceError → cả phép dò hỏng."""
        from aibcr.adapters import wm as WM

        js = WM.WM_DETECT_JS
        self.assertLess(js.index("const evidence"), js.index("evidence.push"))

    def test_a_hidden_lobby_box_does_not_win_over_the_table(self):
        from aibcr.adapters import wm as WM

        js = WM.WM_DETECT_JS
        # isTable ưu tiên dấu hiệu bàn; isLobby chỉ tính khi ``contentLobby`` (đã lọc
        # theo hiển thị) nên hộp sảnh ẩn không kéo kết luận về lobby.
        self.assertIn("(contentTable && !contentLobby && !siblingTable)", js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
