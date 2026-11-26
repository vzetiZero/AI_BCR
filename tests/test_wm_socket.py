# -*- coding: utf-8 -*-
"""WMSocket: cài hook và bắt socket của bàn — kể cả WM nhúng KHÁC ORIGIN.

Vì sao cần bộ test này: lỗi thật đã gặp là app vào được bàn, đọc và phân tích bình
thường nhưng **không đặt được chip**, vì hook ``WebSocket`` chỉ được cài trên tab cổng
trong khi socket của bàn nằm ở target iframe khác origin (hoặc được tạo trước khi hook
chạy). Các test dưới đây khoá lại:

    - hook được cài cho MỌI khung, không dừng ở khung đầu tiên;
    - tab cổng + target iframe đều được gắn và gộp trạng thái đúng chỉ số socket;
    - target iframe chết thì bị gỡ, không giữ trạng thái "sẵn sàng" giả;
    - ``place`` gửi lệnh vào đúng khung của socket được chọn.
"""

import unittest

from aibcr.adapters import wm_targets as WT
from aibcr.adapters import wm_ws


class FakeCommand:
    """Ghi lại lệnh CDP và trả giá trị đặt sẵn theo biểu thức JS."""

    def __init__(self, values=None, fail=False):
        self.calls = []
        self.values = values or {}
        self.fail = fail

    def __call__(self, method, params=None, timeout=None):
        self.calls.append((method, params or {}))
        if self.fail:
            raise RuntimeError("cdp_down")
        if method == "Runtime.evaluate":
            expression = (params or {}).get("expression") or ""
            for key, value in self.values.items():
                if key in expression:
                    return {"result": {"value": value() if callable(value) else value}}
            return {"result": {"value": None}}
        return {}

    def methods(self):
        return [name for name, _params in self.calls]


class FakeSocket:
    def __init__(self, command):
        self.command = command
        self.closed = False
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        self.closed = True


def empty_state(group=None, states=None, scopes=None):
    return {"installed": True, "urls": [], "states": states or [],
            "scopes": scopes or {}, "protocols": {}, "recent21": [],
            "last22": None, "open_group": group, "sent": [], "errors": []}


class InstallEverywhereTests(unittest.TestCase):
    """Hook phải phủ mọi khung: dừng ở khung đầu là mất socket của khung còn lại."""

    def build(self, page_values, oopif_values):
        socket = wm_ws.WMSocket(session=None)
        page_cmd = FakeCommand(page_values)
        oopif_cmd = FakeCommand(oopif_values)
        socket._command = page_cmd
        socket._page = {"label": "page", "command": page_cmd, "ws": None}
        socket._oopif = {"label": "oopif", "command": oopif_cmd, "ws": None,
                         "target": "F1"}
        return socket, page_cmd, oopif_cmd

    def test_hook_runs_in_both_channels(self):
        socket, page_cmd, oopif_cmd = self.build(
            {"querySelectorAll": "ok:1"}, {"querySelectorAll": "ok:1"})
        self.assertEqual(socket.install_everywhere(), 2)
        for cmd in (page_cmd, oopif_cmd):
            self.assertIn("querySelectorAll('iframe')", str(cmd.calls))

    def test_channel_order_prefers_the_embedded_table(self):
        socket, _page, _oopif = self.build({}, {})
        labels = [channel["label"] for channel in socket._channels()]
        self.assertEqual(labels, ["oopif", "page"])


class AttachOopifTests(unittest.TestCase):
    def setUp(self):
        self.original_find = WT.find_wm_frame_target
        self.original_open = WT.open_frame_socket

    def tearDown(self):
        WT.find_wm_frame_target = self.original_find
        WT.open_frame_socket = self.original_open

    def test_no_target_means_no_extra_channel(self):
        class Session:
            wm_oopif_target = ""

        WT.find_wm_frame_target = lambda port=9222: (None, None)
        socket = wm_ws.WMSocket(session=Session())
        self.assertIsNone(socket._attach_oopif())
        self.assertIsNone(socket._oopif)

    def test_uses_the_remembered_target_and_installs_the_hook(self):
        command = FakeCommand()

        class Session:
            wm_oopif_target = "F7"

        WT.open_frame_socket = lambda port=9222, target_id=None, timeout=10.0: \
            FakeSocket(command)
        socket = wm_ws.WMSocket(session=Session())
        channel = socket._attach_oopif()
        self.assertIsNotNone(channel)
        self.assertEqual(channel["target"], "F7")
        methods = command.methods()
        self.assertIn("Runtime.enable", methods)
        self.assertIn("Page.addScriptToEvaluateOnNewDocument", methods)
        # Hook phải chạy NGAY trong document đang sống, không chỉ cho document mới.
        self.assertTrue(any("__wmMakeHook" in str(params.get("expression"))
                            for name, params in command.calls
                            if name == "Runtime.evaluate"))

    def test_attach_is_idempotent(self):
        command = FakeCommand()

        class Session:
            wm_oopif_target = "F7"

        calls = {"n": 0}

        def open_once(port=9222, target_id=None, timeout=10.0):
            calls["n"] += 1
            return FakeSocket(command)

        WT.open_frame_socket = open_once
        socket = wm_ws.WMSocket(session=Session())
        socket._attach_oopif()
        socket._attach_oopif()
        self.assertEqual(calls["n"], 1)


class StateTests(unittest.TestCase):
    def test_dead_embedded_target_is_dropped(self):
        good = FakeCommand({"READ_JS": None})
        bad = FakeCommand(fail=True)
        socket = wm_ws.WMSocket(session=None)
        socket._command = good
        socket._page = {"label": "page", "command": good, "ws": None}
        socket._oopif = {"label": "oopif", "command": bad, "ws": FakeSocket(bad),
                         "target": "F1"}
        socket.state(fresh=True)
        self.assertIsNone(socket._oopif)

    def test_state_without_any_answer_is_not_ready(self):
        socket = wm_ws.WMSocket(session=None)
        socket._state = {"installed": True, "states": [1],
                         "scopes": {0: "iframe"},
                         "recent21": [{"socket": 0, "at": 1, "data": {"groupID": 7}}],
                         "open_group": 7}
        socket._state_at = 1.0
        command = FakeCommand(fail=True)
        socket._command = command
        socket._page = {"label": "page", "command": command, "ws": None}
        self.assertFalse(socket.ready())
        self.assertEqual(socket.state(), {})


class RescueTableSafetyTests(unittest.TestCase):
    def test_missing_socket_never_reloads_an_open_table(self):
        socket = wm_ws.WMSocket(session=None)
        socket.ready = lambda: False
        socket.state = lambda fresh=False: {"open_group": 1352}
        reload_calls = []
        socket.evaluate = lambda *args, **kwargs: reload_calls.append(args) or {
            "ok": True,
        }
        socket._status = lambda *args, **kwargs: None

        self.assertFalse(socket.rescue_table(timeout=0))
        self.assertEqual(reload_calls, [])


class PlaceRoutingTests(unittest.TestCase):
    """Lệnh cược phải đi vào ĐÚNG khung của socket được chọn."""

    def build(self):
        socket = wm_ws.WMSocket(session=None)
        page_cmd = FakeCommand({})
        oopif_cmd = FakeCommand({})
        socket._command = page_cmd
        socket._page = {"label": "page", "command": page_cmd, "ws": None}
        socket._oopif = {"label": "oopif", "command": oopif_cmd, "ws": None,
                         "target": "F1"}
        return socket, page_cmd, oopif_cmd

    def test_bet_goes_to_the_embedded_frame(self):
        socket, page_cmd, oopif_cmd = self.build()
        state = wm_ws.merge_states([
            ("oopif", empty_state(group=1352, states=[1], scopes={0: "iframe"})),
        ])
        state["recent21"] = [{"socket": 0, "at": 9e12,
                              "data": {"groupID": 1352, "gameNo": 1, "gameNoRound": 1}}]
        socket.state = lambda fresh=False: state
        oopif_cmd.values = {
            "socket.send": {"ok": True, "bytes": 10},
            "recent21": {"last22": {"at": 1e18, "data": {"bOk": True}}},
        }
        result = socket.place("P", 10, wait_seconds=1)
        self.assertTrue(result["ok"], result)
        sent = [params for name, params in oopif_cmd.calls
                if name == "Runtime.evaluate" and "socket.send" in
                str(params.get("expression"))]
        self.assertTrue(sent, "lệnh cược phải chạy trong target iframe")
        # Chỉ số socket gửi đi là chỉ số CỤC BỘ của khung, không phải chỉ số đã gộp.
        self.assertIn("const want = 0;", sent[0]["expression"])
        self.assertFalse([params for name, params in page_cmd.calls
                          if "socket.send" in str(params.get("expression"))])

    def test_no_socket_means_explicit_error(self):
        socket, _page, _oopif = self.build()
        socket.state = lambda fresh=False: {"installed": True, "recent21": [],
                                            "states": [], "scopes": {},
                                            "open_group": None}
        result = socket.place("P", 10, wait_seconds=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_socket")


class ReplyMatchingTests(unittest.TestCase):
    """Khớp phản hồi protocol 22 — chỗ từng làm app báo "no_reply" dù đã gửi lệnh.

    Lỗi thật: socket nhận qua ``Runtime.queryObjects`` có chỉ số khác nhau giữa store và
    khung gộp, nên so phản hồi theo chỉ số socket làm phản hồi THẬT bị bỏ qua → tool báo
    "WebSocket từ chối lệnh cược (no_reply)" trong khi tiền đã bị trừ.

    Khung phản hồi THẬT của sàn (bắt được khi cược tay thành công trên bàn 506) là::

        {"gameID": 101, "groupID": 506, "areaID": 506001,
         "areaType": 85, "bOk": true}

    **Không có ``betSerialNumber``, không có ``gameNo``.** Vì vậy khoá nhận phản hồi
    phải là ``groupID``; khớp theo serial chỉ là đường phụ cho mirror khác.
    """

    GROUP = 506
    #: Khung thật, nguyên văn từ sàn.
    REAL_REPLY = {"gameID": 101, "groupID": 506, "areaID": 506001,
                  "areaType": 85, "bOk": True}

    def build(self, reply_data, reply_socket=0, reply_offset=0, top_ok=None,
              reply_at=1e18, group=None):
        socket = wm_ws.WMSocket(session=None)
        command = FakeCommand({})
        socket._command = command
        socket._page = {"label": "page", "command": command, "ws": None}
        table = self.GROUP if group is None else group
        state = wm_ws.merge_states([
            ("page", empty_state(group=table, states=[1], scopes={0: "iframe"})),
        ])
        state["recent21"] = [{"socket": 0, "at": 9e12,
                              "data": {"groupID": table, "gameNo": 5, "gameNoRound": 9}}]
        socket.state = lambda fresh=False: state
        command.values = {
            "socket.send": {"ok": True, "bytes": 10},
            # ``recent21`` trong expression: READ_JS trả ảnh chụp hook.
            "recent21": {"last22": None, "recent22": [
                {"socket": reply_socket, "offset": reply_offset, "at": reply_at,
                 "data": reply_data,
                 # Hook chép sẵn ``ok`` từ cấp ngoài HOẶC trong ``data``.
                 "ok": (reply_data or {}).get("bOk") if top_ok is None else top_ok,
                 "serial": (reply_data or {}).get("betSerialNumber")},
            ]},
        }
        return socket, command

    def test_real_market_reply_is_accepted(self):
        """Khung THẬT của sàn (chỉ groupID + bOk) phải được nhận là thành công.

        Đây là ca đã gây ``no_reply`` trên máy người dùng: phản hồi không có
        ``betSerialNumber`` nên bộ khớp theo serial bỏ qua, rồi báo lỗi oan.
        """
        socket, _command = self.build(dict(self.REAL_REPLY),
                                      # Chỉ số socket lệch — như socket nhận qua
                                      # queryObjects. Không được ảnh hưởng kết quả.
                                      reply_socket=7, reply_offset=5)
        result = socket.place("P", 10, wait_seconds=1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["reply"].get("areaID"), 506001)

    def test_real_market_rejected_bet_is_reported(self):
        """Cùng dạng khung nhưng ``bOk: false`` ⇒ báo sàn từ chối, không phải no_reply."""
        socket, _command = self.build(dict(self.REAL_REPLY, bOk=False),
                                      reply_socket=7, reply_offset=5)
        result = socket.place("P", 10, wait_seconds=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "server_rejected")

    def test_reply_of_another_table_is_not_taken(self):
        """Phản hồi của BÀN KHÁC (groupID khác) không được nhận làm của mình."""
        socket, _command = self.build(dict(self.REAL_REPLY, groupID=999),
                                      reply_at=1e18)
        result = socket.place("P", 10, wait_seconds=0.4)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "no_reply")

    def test_stale_reply_from_before_the_send_is_not_taken(self):
        """Phản hồi cũ (trước lúc gửi) không được nhận — tránh tưởng lệnh mới đã vào."""
        socket, _command = self.build(dict(self.REAL_REPLY), reply_at=1.0)
        result = socket.place("P", 10, wait_seconds=0.4)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "no_reply")

    def test_reply_matched_by_bet_serial_number(self):
        """Mirror có ``betSerialNumber`` vẫn khớp được theo serial."""
        socket, _command = self.build({"bOk": True, "betSerialNumber": 1},
                                      reply_socket=3, reply_offset=3)
        result = socket.place("P", 10, wait_seconds=1)
        self.assertTrue(result["ok"], result)

    def test_ok_flag_at_top_level_is_understood(self):
        """Mirror đặt ``bOk`` ở cấp ngoài (không nằm trong ``data``) vẫn phải hiểu."""
        socket, _command = self.build(None, top_ok=True)
        result = socket.place("P", 10, wait_seconds=1)
        self.assertTrue(result["ok"], result)

    def test_reply_of_another_bet_is_not_taken(self):
        """Phản hồi của lệnh KHÁC (serial khác) không được nhận làm của mình."""
        socket, _command = self.build({"bOk": True, "betSerialNumber": 99})
        result = socket.place("P", 10, wait_seconds=0.4)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "no_reply")

    def test_timeout_says_the_order_was_sent(self):
        """Hết giờ chờ vẫn phải báo ``sent`` để nơi gọi kiểm tra chip thật."""
        socket, _command = self.build(dict(self.REAL_REPLY, groupID=12345))
        result = socket.place("P", 10, wait_seconds=0.4)
        self.assertEqual(result.get("error"), "no_reply")
        self.assertTrue(result.get("sent"), "phải đánh dấu lệnh ĐÃ gửi đi")


class TableSafetyTests(unittest.TestCase):
    """Không bao giờ gửi lệnh cược vào bàn khác bàn đang mở.

    Socket sảnh phát khung 21 của **nhiều bàn cùng lúc**, nên "khung 21 mới nhất" có thể
    thuộc bàn khác. Đặt tiền vào bàn khác là mất tiền không cứu được, nên khi không
    khớp được bàn đang mở (``wm_open_key``) thì phải TỪ CHỐI GỬI.
    """

    class SpyCommand(FakeCommand):
        """FakeCommand ghi lại chỉ số socket nếu ``SEND_JS`` được chạy."""

        def __init__(self, values=None):
            super().__init__(values)
            self.sent_index = None

        def __call__(self, method, params=None, timeout=None):
            expression = (params or {}).get("expression") or ""
            if "const want = " in expression:
                try:
                    self.sent_index = int(expression.split("const want = ")[1].split(";")[0])
                except (IndexError, ValueError):
                    pass
            return super().__call__(method, params, timeout)

    def build(self, open_key, frame_groups, open_group=None):
        socket = wm_ws.WMSocket(session=type("S", (), {"wm_open_key": open_key})())
        command = self.SpyCommand({})
        socket._command = command
        socket._page = {"label": "page", "command": command, "ws": None}
        state = {"installed": True, "urls": [""], "states": [1, 1], "scopes": {},
                 "protocols": {0: 21, 1: 21}, "recent21": [], "last22": None,
                 "recent22": [], "open_group": open_group, "sent": [], "errors": [],
                 "offsets": {}, "seen": {}, "routes": ["page", "page"], "locals": [0, 1]}
        for index, group in enumerate(frame_groups):
            state["recent21"].append({"socket": index, "at": 9e12,
                                      "data": {"groupID": group, "gameNo": 5,
                                               "gameNoRound": 9}})
        socket.state = lambda fresh=False: state
        command.values = {"socket.send": {"ok": True, "bytes": 10}, "recent21": {}}
        return socket, command

    def test_picks_the_frame_of_the_open_table(self):
        """Có khung 21 của nhiều bàn ⇒ phải chọn ĐÚNG bàn đang mở, không phải mới nhất."""
        # Bàn đang mở là 981; khung của bàn 626 cũng có mặt nhưng KHÔNG được chọn.
        socket, command = self.build("101_981", [626, 981])
        socket.place("P", 10, wait_seconds=0.3)
        self.assertEqual(command.sent_index, 1,
                         "phải gửi bằng socket của bàn 981, không phải socket 0 (bàn 626)")

    def test_refuses_when_open_table_has_no_frame(self):
        """Không có khung 21 của bàn đang mở ⇒ TỪ CHỐI, không đoán bàn khác."""
        socket, command = self.build("101_981", [626, 555])
        result = socket.place("P", 10, wait_seconds=0.3)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "table_mismatch")
        # ``SEND_JS`` không được chạy ⇒ không có lệnh cược nào rời máy.
        self.assertIsNone(command.sent_index, "không được gửi lệnh vào bàn khác")


class AdoptExistingSocketTests(unittest.TestCase):
    """Đã vào bàn rồi mới bấm Chạy tool: socket tạo TRƯỚC hook phải nhận được.

    Đây là lỗi làm UI báo "Chưa bắt được WebSocket của bàn WM" dù đang đứng giữa bàn:
    hook ``WebSocket`` chỉ bọc được socket tạo SAU khi cài. Cách sửa là
    ``Runtime.queryObjects`` trên prototype của TỪNG window, không tải lại trang.
    """

    class QueryCommand(FakeCommand):
        """Fake CDP trả objectId theo từng bước của luồng queryObjects."""

        def __init__(self, adopt_count=2, windows=2):
            super().__init__()
            self.adopt_count = adopt_count
            self.windows = windows
            self.queries = 0

        def __call__(self, method, params=None, timeout=None):
            self.calls.append((method, params or {}))
            params = params or {}
            decl = str(params.get("functionDeclaration") or "")
            if method == "Runtime.evaluate":
                return {"result": {"objectId": "list-1"}}
            if method == "Runtime.callFunctionOn":
                if "this.length" in decl:
                    return {"result": {"value": self.windows}}
                if "JSON.stringify" in decl:
                    return {"result": {"value": "{\"top\": true, \"proto\": true}"}}
                if "__wmHook3" in decl:
                    return {"result": {"objectId": "store-1"}}
                if "WebSocket.prototype" in decl:
                    return {"result": {"objectId": "proto-1"}}
                if "return this[i]" in decl:
                    return {"result": {"objectId": "win-1"}}
                if "__wm" in decl or "TextDecoder" in decl:
                    return {"result": {"value": self.adopt_count}}
                return {"result": {"value": self.adopt_count}}
            if method == "Runtime.queryObjects":
                self.queries += 1
                return {"objects": {"objectId": "array-1"}}
            return {}

    def build(self, command):
        socket = wm_ws.WMSocket(session=None)
        socket._command = command
        socket._page = {"label": "page", "command": command, "ws": None}
        return socket

    def test_adopt_queries_the_websocket_prototype_per_window(self):
        command = self.QueryCommand(adopt_count=2, windows=2)
        socket = self.build(command)
        self.assertEqual(socket.adopt_existing(), 4)
        methods = command.methods()
        self.assertIn("Runtime.queryObjects", methods)
        self.assertEqual(command.queries, 2, "phải query RIÊNG từng window")
        # Không được tải lại gì cả: đây là điều kiện an toàn của bàn đang mở.
        self.assertFalse([name for name in methods
                          if name in ("Page.reload", "Page.navigate")])

    def test_adopt_runs_the_hook_in_the_owning_window(self):
        command = self.QueryCommand(adopt_count=1, windows=1)
        socket = self.build(command)
        socket.adopt_existing()
        adopted = [params for name, params in command.calls
                   if name == "Runtime.callFunctionOn"
                   and "__wmHook3" in str(params.get("functionDeclaration"))]
        self.assertTrue(adopted, "store của CHÍNH window sở hữu socket phải được dùng")
        args = [params for name, params in command.calls
                if name == "Runtime.callFunctionOn"
                and "TextDecoder" in str(params.get("functionDeclaration"))]
        self.assertTrue(args, "ADOPT_FN phải chạy trên mảng kết quả của queryObjects")
        self.assertIn("top", str(args[0].get("arguments")))

    def test_place_retries_adoption_when_the_socket_came_first(self):
        """``place`` phải tự cứu socket cũ rồi đặt được, không báo no_socket."""

        class SocketCommand(FakeCommand):
            def __init__(self):
                super().__init__()
                self.state = "before"

            def __call__(self, method, params=None, timeout=None):
                params = params or {}
                expression = str(params.get("expression") or "")
                self.calls.append((method, params))
                if "socket.send" in expression:
                    return {"result": {"value": {"ok": True, "bytes": 10}}}
                if "recent21" in expression and "open_group" in expression:
                    if self.state == "before":
                        return {"result": {"value": {"installed": True, "states": [],
                                                     "scopes": {}, "recent21": [],
                                                     "last22": None,
                                                     "open_group": 7}}}
                    return {"result": {"value": {
                        "installed": True, "states": [1], "scopes": {"0": "iframe"},
                        "recent21": [{"socket": 0, "at": 9e12,
                                      "data": {"groupID": 7, "gameNo": 1,
                                               "gameNoRound": 2}}],
                        "last22": None, "open_group": 7}}}
                if method == "Runtime.queryObjects":
                    return {"objects": {"objectId": "array-1"}}
                if method == "Runtime.evaluate":
                    return {"result": {"objectId": "list-1"}}
                if method == "Runtime.callFunctionOn":
                    decl = str(params.get("functionDeclaration") or "")
                    # ADOPT_FN chứa cả "TextDecoder" lẫn "this.length": kiểm nó TRƯỚC
                    # hàm lấy độ dài mảng, nếu không sẽ khớp nhầm.
                    if "TextDecoder" in decl:
                        self.state = "after"
                        return {"result": {"value": 1}}
                    if "this.length" in decl:
                        return {"result": {"value": 1}}
                    if "JSON.stringify" in decl:
                        return {"result": {"value": "{\"top\": true, \"proto\": true}"}}
                    if "__wmHook3" in decl:
                        return {"result": {"objectId": "store-1"}}
                    if "WebSocket.prototype" in decl:
                        return {"result": {"objectId": "proto-1"}}
                    return {"result": {"objectId": "obj-1"}}
                return {}

        command = SocketCommand()
        socket = wm_ws.WMSocket(session=None)
        socket._command = command
        socket._page = {"label": "page", "command": command, "ws": None}
        socket.wait_ready = lambda timeout=6.0: True
        self.assertFalse(socket.ready())
        result = socket.place("P", 10, wait_seconds=1)
        # Không còn ``no_socket``: socket cũ đã được nhận và lệnh đã gửi đi.
        self.assertNotEqual(result.get("error"), "no_socket", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
