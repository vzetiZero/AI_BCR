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


if __name__ == "__main__":
    unittest.main(verbosity=2)
