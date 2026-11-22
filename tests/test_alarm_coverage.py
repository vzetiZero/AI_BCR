# -*- coding: utf-8 -*-
"""Kiểm thử độ phủ âm báo: mọi sự cố thật phải phát ra log mức `error`.

Renderer chỉ hú còi khi nhận event log có ``level == "error"`` (hoặc thông điệp khớp
mẫu sự cố). Vì vậy backend phải chủ động ghi log lỗi cho các trạng thái dừng do SỰ CỐ
— nếu chỉ đặt ``sim.note`` thì UI im lặng và người dùng không biết.

Thông điệp dừng HỢP LỆ (dừng tool, rời bàn, chốt lời) phải KHÔNG hú còi.
"""

import pathlib
import tempfile
import unittest

from aibcr.controllers.baccarat import BaccaratController
from aibcr.models import engine as E


class SilenceEmitter:
    def __init__(self):
        self.events = []

    def event(self, name, data):
        self.events.append((name, data))


def make():
    folder = pathlib.Path(tempfile.mkdtemp())
    emitter = SilenceEmitter()
    controller = BaccaratController(folder / "state.json", emitter)
    return controller, emitter


def error_logs(emitter):
    return [data["text"] for name, data in emitter.events
            if name == "log" and data.get("level") == "error"]


class SimPauseAlarmTests(unittest.TestCase):
    """`_log_sim_pause` biến trạng thái tạm dừng thành log lỗi cho UI hú còi."""

    def make(self):
        controller, emitter = make()
        self.addCleanup(controller.shutdown)
        with controller.lock:
            controller.sim.table_key = "id:t1"
            controller.sim.table_name = "Bàn 1"
            controller.sim.start({"key": "id:t1", "label": "Bàn 1", "letters": "BP"})
        return controller, emitter

    def test_running_sim_is_silent(self):
        controller, emitter = self.make()
        controller._log_sim_pause()
        self.assertEqual(error_logs(emitter), [])

    def test_data_fault_pauses_ring_the_siren(self):
        controller, emitter = self.make()
        with controller.lock:
            controller.sim.pause("Dữ liệu quá 30 giây chưa cập nhật. Đã dừng để tránh tính sai.")
        controller._log_sim_pause()
        logs = error_logs(emitter)
        self.assertEqual(len(logs), 1)
        self.assertIn("chưa cập nhật", logs[0])

    def test_repeated_same_fault_logs_once(self):
        controller, emitter = self.make()
        note = "Không còn đọc được bàn đang theo dõi. Hãy chọn lại bàn."
        with controller.lock:
            controller.sim.pause(note)
        for _ in range(6):
            controller._log_sim_pause()
        self.assertEqual(len(error_logs(emitter)), 1, "không spam log mỗi nhịp")

    def test_a_different_fault_logs_again(self):
        controller, emitter = self.make()
        with controller.lock:
            controller.sim.pause("Có nhiều cầu khác nhau cùng tên bàn. Cần đối chiếu lại.")
        controller._log_sim_pause()
        with controller.lock:
            controller.sim.pause("Đã bỏ 3 tay liên tiếp vì không đặt được chip thật.")
        controller._log_sim_pause()
        self.assertEqual(len(error_logs(emitter)), 2)

    def test_placing_skips_limit_rings_the_siren(self):
        controller, emitter = self.make()
        with controller.lock:
            controller.sim.pause(
                "Đã bỏ 3 tay liên tiếp vì không đặt được chip thật (không xác nhận "
                "được chip). Dừng để kiểm tra khay chip và ô cược.")
        controller._log_sim_pause()
        logs = error_logs(emitter)
        self.assertEqual(len(logs), 1)
        self.assertIn("không đặt được chip", logs[0])

    def test_legal_user_stops_stay_silent(self):
        """Dừng tool / rời bàn / chốt lời là luồng bình thường, không phải sự cố."""
        for note in ("Đã dừng tool.",
                     "Đang rời bàn…",
                     "Đã rời bàn.",
                     "Đã đạt mục tiêu lãi 25% vốn · rời bàn."):
            with self.subTest(note=note):
                controller, emitter = self.make()
                with controller.lock:
                    controller.sim.pause(note)
                controller._log_sim_pause()
                self.assertEqual(error_logs(emitter), [], note)
                controller.shutdown()

    def test_scan_fault_is_reported_as_an_error(self):
        controller, emitter = make()
        self.addCleanup(controller.shutdown)
        controller._handle_scan([], "No target with given id found", 0.0)
        logs = error_logs(emitter)
        self.assertTrue(logs, "lỗi đọc cầu phải thành log error để hú còi")
        self.assertIn("đọc cầu lỗi", logs[0])


class MoneyBlockAlarmTests(unittest.TestCase):
    """Chốt lời và hết vốn phải phân biệt được để chuông/còi khác nhau."""

    def test_take_profit_and_no_capital_are_distinct_codes(self):
        controller, _ = make()
        self.addCleanup(controller.shutdown)
        book = controller.sim.money
        book.take_profit_pct = 1.0
        book.balance = book.start_capital * 2
        book.check_limits()
        self.assertEqual(book.blocked[0], "TAKE_PROFIT")
        book.balance = book.unit * 0.5
        book.check_limits()
        self.assertEqual(book.blocked[0], "NO_CAPITAL")

    def test_snapshot_exposes_blocked_for_the_renderer(self):
        controller, _ = make()
        self.addCleanup(controller.shutdown)
        book = controller.sim.money
        book.take_profit_pct = 1.0
        book.balance = book.start_capital * 2
        book.check_limits()
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["money"]["blocked"][0], "TAKE_PROFIT")
        self.assertIn("chốt lời", snapshot["money"]["blocked"][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
