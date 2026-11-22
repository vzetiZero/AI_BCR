# -*- coding: utf-8 -*-
"""Bàn xáo bài GIỮA chuỗi thua: thang cược phải mang sang bàn mới.

Kịch bản người dùng yêu cầu:
    mốc 20, chế độ "thắng về mức gốc · thua gấp đôi"
    tay 1: 20 thua → tay 2: 40 thua → tay 3: 80 thua
    tay 4: bàn xáo bài (0 tay) → rời bàn, tìm bàn khác ≥ min_hands
    → bàn mới đặt tiếp **160**, không quay về 20.

Đồng thời: bàn đóng ván trước khi bấm xong chip thì đổi bàn thay vì ngồi lại.
"""

import pathlib
import tempfile
import time
import unittest

from aibcr.controllers.baccarat import BaccaratController
from aibcr.models import engine as E
from aibcr.models.live_sim import progress_stake

MODE = "win_repeat_loss_double"
BASE = 20.0


def road(count):
    return "BP" * (count // 2) + ("B" if count % 2 else "")


def make_controller(min_hands=15):
    folder = pathlib.Path(tempfile.mkdtemp())
    controller = BaccaratController(
        folder / "s.json", type("E", (), {"event": lambda self, n, d: None})())
    controller.config["min_hands"] = min_hands
    controller.config["bet_stake"] = BASE
    controller.config["bet_progression"] = MODE
    controller.bet_config = {"side": "B", "stake": BASE}
    return controller


def row_for(key, label, letters):
    return {"key": key, "label": label, "letters": letters, "ts": time.time(),
            "table_id": key.replace("id:", ""), "read_error": "", "conflict": False,
            "source": "live-ws", "stable": 3}


def play(controller, row, stake, letter):
    """Đặt một tay đã có kết quả (chip đặt thành công)."""
    controller.sim.set_order("B", stake, baseline=controller.sim.letters)
    controller.sim.mark_placing()
    controller.sim.mark_placed(True, "")
    controller.sim.process(dict(row, letters=controller.sim.letters + letter))


class LadderCarryAcrossShoeResetTests(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        self.addCleanup(self.controller.shutdown)
        self.row = row_for("id:t1", "Bàn cũ", road(20))
        self.controller.tables = {"id:t1": self.row}
        with self.controller.lock:
            self.controller.sim.bind(self.row)
            self.controller.sim.start(self.row)
        self.controller.sim.entry_waiting = False
        self.controller.tool_running = True

    def stake_now(self):
        return self.controller._resolve_order()["stake"]

    def test_20_40_80_then_shoe_reset_continues_at_160(self):
        controller = self.controller
        self.assertEqual(self.stake_now(), 20.0)
        play(controller, self.row, 20, "P")
        self.assertEqual(self.stake_now(), 40.0)
        play(controller, self.row, 40, "P")
        self.assertEqual(self.stake_now(), 80.0)
        play(controller, self.row, 80, "P")
        # Mức kế tiếp trước khi xáo bài là 160.
        self.assertEqual(self.stake_now(), 160.0)

        controller.lobby_back = lambda: {"ok": True}
        # Ghi mốc đỉnh khi bàn còn cầu, rồi mới xáo về 0 (bộ theo dõi cần thấy đỉnh > 0).
        controller._watch_shoe_reset(now=900.0)
        controller.tables["id:t1"]["letters"] = ""
        controller._watch_shoe_reset(now=1000.0)
        controller._watch_shoe_reset(now=1000.0 + 6.0)
        self.assertTrue(controller.min_hands_hunt)
        self.assertIsNone(controller.sim.table_key, "phải rời bàn cũ")

        # Bàn mới đủ tay, khác bàn cũ.
        new_row = row_for("id:t2", "Bàn mới", road(24))
        controller.tables["id:t2"] = new_row
        controller.open_table = lambda key: controller.events.put(
            ("table_opened", (True, "Đang ở trong bàn mới", key)))
        controller.auto_open_table()
        controller._drain()
        # Mô phỏng bước xác nhận bàn mới rồi start.
        controller.auto_start_deadline = time.time() + 30
        controller._auto_start_verified_table()
        self.assertTrue(controller.sim.running)
        self.assertEqual(controller.sim.progress_stake, 80.0,
                         "phải mang mức vừa đặt sang bàn mới")
        self.assertEqual(controller.sim.last_outcome, "loss")
        self.assertEqual(self.stake_now(), 160.0,
                         "bàn mới phải đặt tiếp 160, không quay về 20")

    def test_the_carry_is_cleared_after_being_applied_once(self):
        controller = self.controller
        play(controller, self.row, 20, "P")
        controller.ladder_carry = (20.0, "loss")
        restored = controller._restore_ladder_carry()
        self.assertEqual(restored, (20.0, "loss"))
        self.assertIsNone(controller.ladder_carry, "chỉ áp một lần")
        self.assertIsNone(controller._restore_ladder_carry())

    def test_stopping_the_tool_drops_the_carry(self):
        controller = self.controller
        controller.ladder_carry = (80.0, "loss")
        controller.tool_stop()
        self.assertIsNone(controller.ladder_carry,
                          "dừng tool là kết thúc chuỗi, không mang sang lần sau")

    def test_switching_to_a_win_keeps_the_reset_semantics(self):
        """Mang sang bàn mới nhưng tay trước THẮNG thì vẫn về mức gốc."""
        controller = self.controller
        controller.ladder_carry = (80.0, "win")
        controller._restore_ladder_carry()
        self.assertEqual(
            progress_stake(BASE, current=controller.sim.progress_stake,
                           last_outcome=controller.sim.last_outcome, mode=MODE), BASE)

    def test_without_a_carry_the_new_table_starts_from_the_base(self):
        controller = self.controller
        self.assertIsNone(controller._restore_ladder_carry())
        self.assertEqual(self.stake_now(), BASE)


class RotateOnPlaceFailureTests(unittest.TestCase):
    """Bàn đóng ván trước khi bấm xong chip: đổi bàn thay vì ngồi lại."""

    def setUp(self):
        self.controller = make_controller()
        self.addCleanup(self.controller.shutdown)
        self.row = row_for("id:t1", "Bàn cũ", road(20))
        self.controller.tables = {"id:t1": self.row}
        with self.controller.lock:
            self.controller.sim.bind(self.row)
            self.controller.sim.start(self.row)
        self.controller.sim.entry_waiting = False
        self.controller.tool_running = True
        self.controller.config["place_rotate_after"] = 2
        self.left = []
        self.controller.lobby_back = lambda: self.left.append(True) or {"ok": True}

    def report_failed_place(self, note="Ván đã đóng — chip không vào bàn (đã huỷ)."):
        plan = self.controller.sim.set_order("B", 40, baseline=self.controller.sim.letters)
        self.controller.sim.mark_placing()
        self.controller.events.put(("placed", (False, note, plan)))
        self.controller._drain()

    def test_one_failure_keeps_the_table(self):
        self.report_failed_place()
        self.assertEqual(self.left, [])
        self.assertEqual(self.controller.place_fail_streak, 1)

    def test_two_failures_leave_the_table(self):
        self.report_failed_place()
        self.report_failed_place()
        self.assertEqual(self.left, [True], "phải rời bàn tìm bàn khác")
        self.assertIsNone(self.controller.sim.table_key)
        self.assertTrue(self.controller.cycle_waiting)
        self.assertEqual(self.controller.cycle_table_key, "id:t1",
                         "không được mở lại chính bàn vừa rời")
        self.assertIn("tìm bàn khác", self.controller.sweep_note.lower())

    def test_a_successful_place_resets_the_streak(self):
        self.report_failed_place()
        self.assertEqual(self.controller.place_fail_streak, 1)
        plan = self.controller.sim.set_order("B", 40, baseline=self.controller.sim.letters)
        self.controller.sim.mark_placing()
        self.controller.events.put(("placed", (True, "Đặt chip OK", plan, 40.0)))
        self.controller._drain()
        self.assertEqual(self.controller.place_fail_streak, 0)
        self.assertEqual(self.controller.place_rotations, 0)
        self.assertEqual(self.left, [])

    def test_rotations_are_capped(self):
        self.controller.place_rotations = 3
        self.report_failed_place()
        self.report_failed_place()
        self.assertEqual(self.left, [], "quá 3 lần thì dừng, không quay vòng vô hạn")
        self.assertFalse(self.controller.sim.running)

    def test_rotation_is_off_when_the_setting_is_zero(self):
        self.controller.config["place_rotate_after"] = 0
        for _ in range(4):
            self.report_failed_place()
        self.assertEqual(self.left, [])
        self.assertTrue(self.controller.sim.running)


if __name__ == "__main__":
    unittest.main(verbosity=2)
