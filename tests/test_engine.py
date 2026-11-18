# -*- coding: utf-8 -*-
"""Kiểm thử engine JSON của Electron (aibcr/service/engine.py).

Không cần Chrome: bàn/Chrome được thay bằng đối tượng giả. Mục tiêu là khoá lại hợp
đồng giữa UI Electron và tiến trình Python của AI_BCR:

    - chỉ có một dịch vụ Baccarat, dispatch đúng method, method lạ báo lỗi
    - nút “VÀO BÀN HỢP LỆ” tìm + mở + xác nhận bàn nhưng KHÔNG đặt cược
    - lọc bàn theo 5 điều kiện (lệch B/P, bệt, cầu 3-3, khoảng tay, hoà ưu tiên đỏ)
    - đóng Chrome bằng tay thì app thấy mất kết nối và còn đường mở lại
"""

import json
import pathlib
import tempfile
import time
import unittest

from aibcr.adapters import browser as BR
from aibcr.service import engine


class FakeEmitter:
    def __init__(self):
        self.events = []

    def event(self, name, data):
        self.events.append((name, data))

    def reply(self, request_id, result=None, error=None):
        self.events.append(("reply", {"id": request_id, "result": result, "error": error}))

    def send(self, payload):
        self.events.append(("send", payload))


def make_service(config=None, letters=None, now=None, source="evo-ws"):
    """Dịch vụ Baccarat với Chrome giả đang kết nối và một bàn đã đọc được."""
    folder = pathlib.Path(tempfile.mkdtemp())
    service = engine.BaccaratService(folder / "state.json", FakeEmitter())
    if config:
        result = service.config_set(config)
        assert result.get("ok"), result
    service.browser = type("FakeBrowser", (), {
        "connected": True, "note": "", "mode": "cdp", "cdp_attached": True,
        "stop": lambda self: None,
    })()
    if letters is not None:
        stamp = now if now is not None else time.time()
        service.tables = {"id:t1": {
            "key": "id:t1", "label": "Bàn 1", "letters": letters, "ts": stamp,
            "table_id": "t1", "game_type": "", "read_error": "", "source": source,
            "conflict": False, "stable": 3, "screen_stable": 3,
            "ws_confirmed_letters": letters,
        }}
    return service


class DispatchTests(unittest.TestCase):
    def test_ping_and_state_are_answered(self):
        service = make_service()
        result, error = engine.dispatch(service, "ping", {})
        self.assertIsNone(error)
        self.assertTrue(result["pong"])
        self.assertEqual(result["game"], "baccarat")
        snapshot, error = engine.dispatch(service, "state", {})
        self.assertIsNone(error)
        self.assertEqual(snapshot["game"], "baccarat")
        self.assertFalse(snapshot["tool_running"])

    def test_unknown_method_reports_an_error(self):
        service = make_service()
        result, error = engine.dispatch(service, "khong.co", {})
        self.assertIsNone(result)
        self.assertIn("không hỗ trợ", error)

    def test_only_baccarat_is_supported(self):
        emit = FakeEmitter()
        baccarat = engine.build_service("baccarat", emit,
                                       pathlib.Path(tempfile.mkdtemp()) / "b.json")
        self.assertIsInstance(baccarat, engine.BaccaratService)
        with self.assertRaises(SystemExit):
            engine.build_service("longlong", emit,
                                 pathlib.Path(tempfile.mkdtemp()) / "l.json")

    def test_dispatch_exposes_the_enter_valid_command(self):
        """UI chỉ gọi đúng một lệnh để vào bàn hợp lệ."""
        service = make_service()
        methods = []
        service.enter_valid_table = lambda: methods.append("enter") or {"ok": True}
        reply, error = engine.dispatch(service, "table.enterValid", {})
        self.assertIsNone(error)
        self.assertTrue(reply["ok"])
        self.assertEqual(methods, ["enter"])

    def test_dispatch_exposes_cancel_enter_valid(self):
        service = make_service()
        methods = []
        service.cancel_enter_valid = lambda: methods.append("cancel") or {"ok": True}
        reply, error = engine.dispatch(service, "table.cancelEnter", {})
        self.assertIsNone(error)
        self.assertTrue(reply["ok"])
        self.assertEqual(methods, ["cancel"])


class ConfigTests(unittest.TestCase):
    def test_config_set_persists_and_applies_the_lead_window(self):
        service = make_service()
        reply = service.config_set({"min_lead": 3, "max_lead": 4, "tie_side": "P"})
        self.assertTrue(reply["ok"], reply)
        saved = json.loads(service.state_path.read_text(encoding="utf-8"))["settings"]
        self.assertEqual(saved["max_lead"], 4)
        self.assertEqual(saved["tie_side"], "P")
        self.assertEqual((service.sim.min_lead, service.sim.max_lead), (3, 4))
        self.assertEqual(service.sim.tie_side, "P")

    def test_invalid_start_url_falls_back_to_the_default(self):
        service = make_service()
        reply = service.config_set({"start_url": "khong-phai-link"})
        self.assertTrue(reply.get("ok"), reply)
        self.assertTrue(service.config["start_url"].startswith("http"))

    def test_window_is_clamped_and_persisted(self):
        service = make_service()
        reply = service.apply_window({"width": 99999, "height": 10, "percent": 500,
                                      "fullscreen": True})
        self.assertTrue(reply["ok"])
        window = service.config["chrome_window"]
        self.assertTrue(window["fullscreen"])
        self.assertEqual(window["width"], BR.SIZE_MAX[0])
        self.assertEqual(window["height"], BR.SIZE_MIN[1])
        self.assertEqual(window["percent"], BR.SCALE_MAX)
        saved = json.loads(service.state_path.read_text(encoding="utf-8"))["settings"]
        self.assertEqual(saved["chrome_window"], window)


class ToolLifecycleTests(unittest.TestCase):
    def test_tool_starts_even_without_chrome_by_hunting_a_table(self):
        """Bật tool là tự vào bàn hợp lệ: thiếu Chrome thì luồng vào bàn tự gắn/mở."""
        service = make_service()
        service.browser = type("FakeBrowser", (), {
            "connected": False, "note": "", "mode": "cdp", "cdp_attached": False,
            "attach": lambda self: False, "stop": lambda self: None,
        })()
        service.enter_valid_table = lambda: {"ok": True, "note": "hunting"}
        result = service.tool_start()
        self.assertTrue(result["ok"], result)
        self.assertTrue(service.tool_running)


class BaccaratEnterValidTests(unittest.TestCase):
    """Nút duy nhất “VÀO BÀN HỢP LỆ”: tìm + mở + xác nhận nhưng KHÔNG cược."""

    # 22 tay: B 13 · P 9 (lệch 4), không bệt quá 3, không có cầu 3-3 → đạt cả 5 điều kiện.
    VALID = "".join(side * count for side, count in (
        ("B", 3), ("P", 2), ("B", 3), ("P", 2), ("B", 2),
        ("P", 3), ("B", 2), ("P", 2), ("B", 3)))

    def service(self, letters=None, config=None):
        return make_service(config=config, letters=letters or self.VALID, source="evo-bead")

    def prime(self, service, letters=None):
        """Giả lập: đã bấm ô bàn và cầu trong bàn đã xác nhận cùng shoe."""
        letters = service.tables["id:t1"]["letters"] if letters is None else letters
        service.enter_valid = True
        service.valid_check_done = False
        service.auto_start_key = "id:t1"
        service.auto_start_letters = letters
        service.auto_start_deadline = time.time() + 60

    def test_enter_valid_binds_the_table_without_starting_a_session(self):
        service = self.service()
        self.prime(service)
        service._auto_start_verified_table()
        self.assertTrue(service.valid_check_done)
        self.assertFalse(service.enter_valid)
        self.assertEqual(service.sim.table_key, "id:t1")
        self.assertFalse(service.sim.running, "chưa bật tool thì chưa chạy phiên")
        self.assertIsNone(service.sim.pending, "chưa được arm kế hoạch cược")
        self.assertFalse(service.place_busy, "không được đặt chip")
        self.assertIn("CHƯA đặt cược", service.valid_note)

    def test_enter_valid_rejects_a_different_shoe(self):
        service = self.service()
        self.prime(service, letters="PPPBBB")
        service._auto_start_verified_table()
        self.assertFalse(service.valid_check_done)
        self.assertIsNone(service.auto_start_key, "phải bỏ bàn khác shoe")
        self.assertFalse(service.sim.running)

    def test_tool_start_arms_the_verified_table(self):
        service = self.service()
        self.prime(service)
        service._auto_start_verified_table()
        reply = service.tool_start()
        self.assertTrue(reply["ok"])
        self.assertTrue(service.sim.running)
        self.assertTrue(service.sim.entry_waiting, "vẫn chờ hết tay hiện tại mới cược")
        self.assertIsNone(service.sim.pending)

    def test_enter_valid_hunts_without_the_tool(self):
        service = self.service()
        service.enter_valid = True
        called = []
        service.open_table = lambda key: called.append(key) or {"ok": True}
        service.auto_open_table(allow_sweep=False)
        self.assertEqual(called, ["id:t1"], "phải mở bàn đạt chuẩn dù tool chưa chạy")

    def test_auto_hunt_alone_still_needs_the_tool(self):
        service = self.service()
        called = []
        service.open_table = lambda key: called.append(key) or {"ok": True}
        service.auto_open_table(allow_sweep=False)
        self.assertEqual(called, [], "không bật tool thì không tự mở bàn")

    def test_cancel_enter_valid_stops_the_hunt(self):
        service = self.service()
        service.enter_valid = True
        service.cycle_waiting = True
        reply = service.cancel_enter_valid()
        self.assertTrue(reply["ok"])
        self.assertFalse(service.enter_valid)
        self.assertFalse(service.cycle_waiting)
        self.assertIn("huỷ", service.valid_note.casefold())

    def test_enter_valid_times_out(self):
        service = self.service()
        service.enter_valid = True
        service.enter_deadline = time.time() - 1
        service._watch_enter_valid(time.time())
        self.assertFalse(service.enter_valid)
        self.assertFalse(service.cycle_waiting)
        self.assertIn("Chưa vào được bàn", service.valid_note)

    def test_snapshot_exposes_enter_valid_state(self):
        service = self.service()
        service.enter_valid = True
        service.enter_deadline = time.time() + 30
        block = service.snapshot()["enter_valid"]
        self.assertTrue(block["active"])
        self.assertFalse(block["checked"])
        self.assertGreater(block["remain"], 0)

    def test_tool_start_without_chrome_enters_valid_flow(self):
        service = self.service()
        service.browser = type("FakeBrowser", (), {
            "connected": False, "note": "", "mode": "cdp", "cdp_attached": False,
            "attach": lambda self: False, "stop": lambda self: None,
        })()
        reply = service.tool_start()
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(service.tool_running)
        self.assertTrue(service.enter_valid, "bật tool phải tự tìm bàn hợp lệ")
        service.cancel_enter_valid()


class BaccaratEngineTests(unittest.TestCase):
    def test_scan_maps_browser_reports_to_rows(self):
        service = make_service()
        service.browser = type("FakeBrowser", (), {
            "connected": True,
            "scan": lambda self: [{"tables": [{"id": "t1", "title": "Bàn 1",
                                               "letters": "BBBPPP"}]}],
        })()
        snapshot = service.scan()
        self.assertEqual(snapshot["tables"][0]["id"], "t1")
        self.assertEqual(snapshot["tables"][0]["letters"], "BBBPPP")


class BaccaratTableFilterTests(unittest.TestCase):
    """Lọc bàn Baccarat: hoà điểm ưu tiên bên đỏ, chặn lệch B/P quá lớn (4-5 tay)."""

    @staticmethod
    def road(*runs):
        """Ghép cầu từ các vệt (ký tự, số tay) để dễ kiểm soát số B/P và bệt."""
        return "".join(side * count for side, count in runs)

    # B 13 · P 9 (lệch 4), không bệt quá 3 tay, không có hai vệt 3 liền nhau.
    LEAD4 = road(("B", 3), ("P", 2), ("B", 3), ("P", 2), ("B", 2), ("P", 3),
                 ("B", 2), ("P", 2), ("B", 3))
    # B 20 · P 10 (lệch 10) — vượt mức tối đa mặc định.
    LEAD10 = road(("B", 3), ("P", 2), ("B", 2), ("P", 1), ("B", 3), ("P", 2),
                  ("B", 2), ("P", 1), ("B", 3), ("P", 2), ("B", 2), ("P", 1),
                  ("B", 3), ("P", 1), ("B", 2))
    # Hoà điểm B 8 · P 8, vệt cuối là Player.
    TIE = road(("B", 2), ("P", 3), ("B", 2), ("P", 2), ("B", 2), ("P", 2),
               ("B", 2), ("P", 1))

    def service(self, letters, config=None):
        return make_service(config=config, letters=letters)

    def test_default_window_accepts_a_four_hand_gap(self):
        row = self.service(self.LEAD4).table_rows()[0]
        self.assertEqual((row["banker"], row["player"], row["lead"]), (13, 9, 4))
        self.assertTrue(row["eligible"], row["verdict"])
        self.assertTrue(row["verdict"].startswith("Đạt 5/5"), row["verdict"])

    def test_gap_bigger_than_max_lead_is_rejected(self):
        row = self.service(self.LEAD10).table_rows()[0]
        self.assertEqual((row["banker"], row["player"], row["lead"]), (20, 10, 10))
        self.assertFalse(row["eligible"])
        self.assertIn("chỉ nhận ≤ 5", row["verdict"])

    def test_config_can_widen_or_drop_the_upper_bound(self):
        wide = self.service(self.LEAD10, {"max_lead": 10}).table_rows()[0]
        self.assertTrue(wide["eligible"], wide["verdict"])
        unlimited = self.service(self.LEAD10, {"max_lead": None}).table_rows()[0]
        self.assertTrue(unlimited["eligible"], unlimited["verdict"])
        self.assertEqual(len(unlimited["checks"]), 4)   # không còn điều kiện trần

    def test_tie_prefers_red_banker_by_default(self):
        row = self.service(self.TIE).table_rows()[0]
        self.assertEqual((row["banker"], row["player"], row["lead"]), (8, 8, 0))
        self.assertTrue(row["tie_break"])
        self.assertEqual(row["strong"], "B")
        self.assertEqual(row["tie_side"], "B")
        self.assertFalse(row["eligible"])            # lệch 0 vẫn dưới mức tối thiểu

    def test_tie_can_follow_the_running_streak(self):
        row = self.service(self.TIE, {"tie_side": "run"}).table_rows()[0]
        self.assertEqual(row["strong"], "P")         # vệt cuối là Player
        self.assertEqual(row["tie_side"], "run")

    def test_new_session_keeps_the_lead_window(self):
        service = self.service(self.LEAD4, {"max_lead": 4, "tie_side": "P"})
        self.assertTrue(service.session_new({"capital": 2500})["ok"])
        self.assertEqual((service.sim.min_lead, service.sim.max_lead), (3, 4))
        self.assertEqual(service.sim.tie_side, "P")


class ChromeClosedRecoveryTests(unittest.TestCase):
    """Đóng Chrome bằng tay: app phải thấy mất kết nối và còn đường mở lại."""

    @staticmethod
    def fake_browser(connected=False, note=""):
        return type("FakeBrowser", (), {"connected": connected, "note": note,
                                        "mode": "cdp" if connected else "",
                                        "stop": lambda self: None})()

    def baccarat(self):
        now = time.time()
        service = make_service(letters="BBBP")
        service.tables["id:t1"]["ts"] = now
        service.tool_running = True
        return service

    def test_clears_tables_and_pauses_when_chrome_closes(self):
        service = self.baccarat()
        service.browser = self.fake_browser(connected=True)
        service._on_chrome_lost()
        self.assertEqual(service.tables, {})
        self.assertIn("Mở Chrome", service.sweep_note)
        self.assertTrue(service.offline_noted)

    def test_snapshot_reports_disconnected(self):
        service = self.baccarat()
        service.browser = self.fake_browser(connected=False, note="Chrome đã đóng")
        snapshot = service.snapshot()
        self.assertFalse(snapshot["chrome_connected"])
        self.assertEqual(snapshot["tables"], [])
        self.assertEqual(snapshot["chrome_note"], "Chrome đã đóng")

    def test_ready_flag_resets_after_reconnect(self):
        service = self.baccarat()
        service._on_chrome_lost()
        self.assertTrue(service.offline_noted)
        service._on_chrome_ready()
        self.assertFalse(service.offline_noted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
