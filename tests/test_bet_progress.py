# -*- coding: utf-8 -*-
"""Thang cược phải theo SỐ TIỀN THẬT đã lên bàn, không theo số dự định.

Lỗi thật đã gặp: cấu hình cố định 50, thang `win_repeat_loss_double`; lệnh 200 nhưng
sàn chỉ nhận 100 (trần bàn/ví). Khi tay đó THẮNG, chương trình vẫn ghi 200 là mức đã
đặt, nên:
  · lãi/lỗ tính theo 200 (gấp đôi thực tế);
  · thang cược tưởng "vừa thắng ở 200" nhưng lại đang giữ `last_outcome` cũ, và mức bị
    trần cắt còn 100 → tay sau đặt tiếp 100 mãi, không bao giờ về 50.

Ngoài ra: tay KHÔNG đặt được chip không phải thua, nhưng cũng không được xoá mức cược
đang chờ — thua ở 10 thì tay kế vẫn phải là 20. Chỉ khi lỗi lặp 3 tay liền
(``PLACE_SKIP_LIMIT``) mới dừng tool để kiểm tra.
"""

import pathlib
import tempfile
import time
import unittest

from aibcr.controllers.baccarat import BaccaratController
from aibcr.models import engine as E
from aibcr.models import money as M
from aibcr.models.live_sim import LiveSimulation, progress_stake

MODE = "win_repeat_loss_double"


def road(count):
    return "BP" * (count // 2) + ("B" if count % 2 else "")


def make_sim(capital=1000.0, unit=50.0):
    book = M.MoneyBook(capital=capital, unit=unit)
    sim = LiveSimulation(book, unit=unit)
    row = {"key": "id:t1", "label": "Bàn 1", "letters": road(4), "ts": time.time(),
           "table_id": "t1", "stable": 3}
    sim.bind(row)
    sim.start(row)
    sim.entry_waiting = False
    return sim, row


def play(sim, row, stake, letter, ok=True, placed_stake=None):
    """Đặt một tay: chốt lệnh → bấm chip → có kết quả."""
    base = sim.letters
    sim.set_order(E.BANKER, stake, baseline=base)
    sim.mark_placing()
    sim.mark_placed(ok, "" if ok else "không bấm được", placed_stake=placed_stake)
    sim.process(dict(row, letters=base + letter))


class SettledStakeTests(unittest.TestCase):
    """`mark_placed(placed_stake=...)` + `settled_stake` là nguồn số liệu duy nhất."""

    def test_settled_stake_prefers_the_confirmed_amount(self):
        sim, row = make_sim()
        sim.set_order(E.BANKER, 200, baseline=sim.letters)
        sim.mark_placing()
        sim.mark_placed(True, "", placed_stake=100)
        order = sim.pending
        self.assertEqual(sim.settled_stake(order), 100.0)

    def test_settled_stake_falls_back_to_the_request(self):
        sim, row = make_sim()
        sim.set_order(E.BANKER, 200, baseline=sim.letters)
        sim.mark_placing()
        sim.mark_placed(True, "")
        self.assertEqual(sim.settled_stake(sim.pending), 200.0)

    def test_invalid_confirmed_amount_is_ignored(self):
        sim, _row = make_sim()
        sim.set_order(E.BANKER, 200, baseline=sim.letters)
        sim.mark_placing()
        sim.mark_placed(True, "", placed_stake=None)
        self.assertEqual(sim.pending.get("placed_stake"), None)
        self.assertEqual(sim.settled_stake(sim.pending), 200.0)


class ShortPlacementTests(unittest.TestCase):
    """Lệnh 200 nhưng bàn chỉ nhận 100: mọi con số phải theo 100."""

    def test_win_is_paid_on_the_actual_amount(self):
        sim, _row = make_sim()
        before = sim.money.balance
        play(sim, _row, 200, "B", placed_stake=100)
        # Banker thắng 0,95 → 100 × 0,95 = 95, KHÔNG phải 200 × 0,95 = 190.
        self.assertAlmostEqual(sim.money.balance - before, 95.0, places=2)

    def test_ladder_records_the_actual_amount(self):
        sim, row = make_sim()
        play(sim, row, 200, "B", placed_stake=100)
        self.assertEqual(sim.progress_stake, 100.0)
        self.assertEqual(sim.last_outcome, "win")
        # Thắng → tay sau về đúng mức gốc 50, không giữ 100 hay 200.
        self.assertEqual(progress_stake(50, current=sim.progress_stake,
                                        last_outcome=sim.last_outcome, mode=MODE), 50.0)

    def test_history_row_records_the_actual_amount(self):
        sim, row = make_sim()
        play(sim, row, 200, "B", placed_stake=100)
        row_log = sim.log[-1]
        self.assertEqual(row_log["stake"], 100.0,
                         "lịch sử phải ghi số thật để đối chiếu với web")

    def test_the_reported_loop_is_gone(self):
        """Chuỗi gốc: 50 thua → 100 thua → 200 (bàn chỉ nhận 100) thắng → phải về 50."""
        controller = BaccaratController(
            pathlib.Path(tempfile.mkdtemp()) / "s.json",
            type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.dispatch("bet.config", {"side": "B", "stake": 50,
                                          "progression": MODE, "max_stake": 100})
        row = {"key": "id:t1", "label": "Bàn 1", "letters": road(4),
               "ts": time.time(), "table_id": "t1", "stable": 3}
        controller.tables = {"id:t1": row}
        controller.sim.bind(row)
        controller.sim.start(row)
        controller.sim.entry_waiting = False
        controller.tool_running = True

        def stake_now():
            return controller._resolve_order()["stake"]

        def place_order(stake, letter, placed_stake=None):
            controller.sim.set_order("B", stake, baseline=controller.sim.letters)
            controller.sim.mark_placing()
            controller.sim.mark_placed(True, "", placed_stake=placed_stake)
            controller.sim.process(dict(row, letters=controller.sim.letters + letter))

        self.assertEqual(stake_now(), 50.0)
        place_order(50, "P")
        self.assertEqual(stake_now(), 100.0)
        place_order(100, "P")
        # Muốn 200 nhưng trần 100: hệ thống đặt 100.
        self.assertEqual(stake_now(), 100.0)
        place_order(100, "B")
        self.assertEqual(stake_now(), 50.0,
                         "thắng ở 100 phải về mức gốc 50, không lặp lại 100")


class FailedPlacementKeepsTheLadderTests(unittest.TestCase):
    """Tay không đặt được chip KHÔNG phải thua, nhưng cũng KHÔNG xoá mức đang chờ.

    Đây là yêu cầu thực tế: thua ở mức 10 ⇒ tay kế phải là 20. Nếu lượt đặt 20 lỗi
    (WebSocket không khớp phản hồi), tay sau vẫn phải thử 20 — không được âm thầm rơi về
    10, vì như vậy chuỗi "thua gấp đôi" mất tác dụng và khoản lỗ trước đó không bao giờ
    được gỡ. Vòng lặp vô hạn đã bị chặn bằng ``PLACE_SKIP_LIMIT`` (3 tay liền) nên giữ
    mức là an toàn.
    """

    def test_skip_keeps_the_pending_level(self):
        sim, row = make_sim()
        play(sim, row, 50, "P")            # thua 50 → mức kế 100
        self.assertEqual(sim.progress_stake, 50.0)
        self.assertEqual(sim.last_outcome, "loss")
        play(sim, row, 100, "P", ok=False)  # không đặt được
        self.assertEqual(sim.progress_stake, 100.0,
                         "phải giữ đúng mức vừa chờ (100), không rơi về 50")
        self.assertIsNone(sim.last_outcome,
                          "lượt không đặt được không có kết quả để nhân đôi tiếp")
        self.assertEqual(progress_stake(50, current=sim.progress_stake,
                                        last_outcome=sim.last_outcome, mode=MODE), 100.0,
                         "tay kế vẫn phải đặt 100, không quay về 50 và không leo 200")

    def test_repeated_failures_keep_retrying_the_doubled_amount(self):
        controller = BaccaratController(
            pathlib.Path(tempfile.mkdtemp()) / "s.json",
            type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.dispatch("bet.config", {"side": "B", "stake": 50,
                                          "progression": MODE})
        row = {"key": "id:t1", "label": "Bàn 1", "letters": road(4),
               "ts": time.time(), "table_id": "t1", "stable": 3}
        controller.tables = {"id:t1": row}
        controller.sim.bind(row)
        controller.sim.start(row)
        controller.sim.entry_waiting = False
        controller.tool_running = True
        # Tay đầu đặt đúng mức gốc 50 và thua ⇒ mức chờ là 100.
        controller.sim.set_order("B", 50, baseline=controller.sim.letters)
        controller.sim.mark_placing()
        controller.sim.mark_placed(True, "")
        controller.sim.process(dict(row, letters=controller.sim.letters + "P"))
        self.assertEqual(controller._resolve_order()["stake"], 100.0)
        for _ in range(2):
            stake = controller._resolve_order()["stake"]
            controller.sim.set_order("B", stake, baseline=controller.sim.letters)
            controller.sim.mark_placing()
            controller.sim.mark_placed(False, "không bấm được")
            controller.sim.process(dict(row, letters=controller.sim.letters + "P"))
        self.assertEqual(controller._resolve_order()["stake"], 100.0,
                         "sau tay không đặt được, vẫn phải thử mức đang chờ (100)")

    def test_three_failures_in_a_row_stop_the_tool(self):
        """Giữ mức không được biến thành vòng lặp vô hạn: đủ 3 tay là dừng để kiểm tra."""
        sim, row = make_sim()
        play(sim, row, 50, "P")
        for _ in range(3):
            play(sim, row, 100, "P", ok=False)
        self.assertFalse(sim.running)
        self.assertIn("3 tay liên tiếp", sim.note)


class PlacementStatusTests(unittest.TestCase):
    """UI phải biết lệnh bị sàn cắt để người dùng không tưởng đã đặt đủ."""

    def test_short_placement_is_reported(self):
        controller = BaccaratController(
            pathlib.Path(tempfile.mkdtemp()) / "s.json",
            type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        row = {"key": "id:t1", "label": "Bàn 1", "letters": road(4),
               "ts": time.time(), "table_id": "t1", "stable": 3}
        controller.tables = {"id:t1": row}
        controller.sim.bind(row)
        controller.sim.start(row)
        controller.sim.entry_waiting = False
        plan = controller.sim.set_order("B", 200, baseline=controller.sim.letters)
        controller.sim.mark_placing()
        controller.events.put(("placed", (True, "Đã đặt", plan, 100.0)))
        controller._drain()
        self.assertTrue(controller.place_status["short"])
        self.assertEqual(controller.place_status["placed_stake"], 100.0)
        self.assertEqual(controller.place_status["requested_stake"], 200.0)
        self.assertIn("chỉ nhận", controller.place_status["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
