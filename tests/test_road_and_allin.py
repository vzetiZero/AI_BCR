# -*- coding: utf-8 -*-
"""Hai yêu cầu: đi theo cầu bệt/1:1, và all-in khi số dư cuối không chia khít chip.

Lỗi thật đã gặp:
  · Cầu bệt Player 6 tay nhưng AUTO vẫn đặt Banker 6 tay (thua trắng) — vì nguồn cửa
    mặc định là ``probability`` và Banker luôn cao xác suất hơn trên shoe đầy.
  · Ví còn 636 đv, lệnh cần 640 → ``decompose`` báo không chia khít nên bỏ tay, thay vì
    all-in 630 (500+100+20+10).
"""

import pathlib
import tempfile
import time
import unittest

from aibcr.controllers.baccarat import BaccaratController
from aibcr.live import predict as P
from aibcr.models import chips


def road(count):
    return "BP" * (count // 2) + ("B" if count % 2 else "")


class FollowTheRoadTests(unittest.TestCase):
    def test_bet_player_run_is_followed_not_reversed(self):
        """Đúng ảnh chụp: 6 tay Player liền nhau → phải đánh P."""
        history = "PBPPBTTPPBBPPBPPPPPP"
        follow = P.road_follow(history)
        self.assertEqual(follow["pattern"], "bệt")
        self.assertEqual(follow["side"], "P")

    def test_one_by_one_road_flips_to_the_other_side(self):
        follow = P.road_follow("BPBPBPBPBPBP")
        self.assertEqual(follow["pattern"], "1:1")
        self.assertEqual(follow["side"], "B", "tay cuối P → 1:1 đánh B")

    def test_hybrid_is_the_default_source(self):
        controller = BaccaratController(
            pathlib.Path(tempfile.mkdtemp()) / "s.json",
            type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        self.assertEqual(controller.config["auto_side_source"], "hybrid")

    def test_hybrid_uses_the_road_over_probability(self):
        controller = BaccaratController(
            pathlib.Path(tempfile.mkdtemp()) / "s.json",
            type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.config["auto_side_source"] = "hybrid"
        row = {"key": "id:t1", "label": "Bàn", "letters": road(4), "ts": time.time(),
               "table_id": "t1", "stable": 3}
        controller.tables = {"id:t1": row}
        with controller.lock:
            controller.sim.bind(row)
            controller.sim.start(row)

        class FakeLive:
            def snapshot(self):
                return {
                    "running": True, "table": "WM t1", "hand": 22,
                    # 6 tay Player liền nhau ở cuối cầu.
                    "history": "PBPPBTTPPBBPPBPPPPPP",
                    "prediction": {
                        "ok": True, "top_side": "B", "playable": False,
                        "probabilities": {"p_player": 0.44, "p_banker": 0.46,
                                          "p_tie": 0.10},
                        "road_follow": {"side": "P", "pattern": "bệt",
                                        "reason": "Cầu bệt P 6 tay"},
                    },
                    "analysis": {},
                }

        controller.live_session = FakeLive()
        order = controller._resolve_order()
        self.assertEqual(order["side"], "P", "phải đi theo cầu bệt Player")
        self.assertEqual(order["auto_source"], "road")

    def test_probability_mode_still_available_for_manual_choice(self):
        controller = BaccaratController(
            pathlib.Path(tempfile.mkdtemp()) / "s.json",
            type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.config["auto_side_source"] = "probability"
        row = {"key": "id:t1", "label": "Bàn", "letters": road(4), "ts": time.time(),
               "table_id": "t1", "stable": 3}
        controller.tables = {"id:t1": row}
        with controller.lock:
            controller.sim.bind(row)
            controller.sim.start(row)

        class FakeLive:
            def snapshot(self):
                return {"running": True, "table": "WM t1", "hand": 22,
                        "history": "PBPPBTTPPBBPPBPPPPPP",
                        "prediction": {"ok": True, "top_side": "B",
                                       "probabilities": {"p_player": 0.44,
                                                         "p_banker": 0.46,
                                                         "p_tie": 0.10},
                                       "road_follow": {"side": "P", "pattern": "bệt"}},
                        "analysis": {}}

        controller.live_session = FakeLive()
        self.assertEqual(controller._resolve_order()["side"], "B")


class AllInChipTests(unittest.TestCase):
    DENOMS = (10, 20, 50, 100, 500, 1000, 5000, 10000)

    def test_636_rounds_down_to_630(self):
        self.assertEqual(chips.affordable(636, 1, self.DENOMS), 630)

    def test_the_all_in_amount_is_fully_decomposable(self):
        amount = chips.affordable(636, 1, self.DENOMS)
        plan = chips.decompose(amount, 1, self.DENOMS)
        self.assertTrue(plan["exact"])
        self.assertEqual(plan["chips"], [500, 100, 20, 10])
        self.assertEqual(sum(plan["chips"]), 630)

    def test_an_exact_amount_is_untouched(self):
        self.assertEqual(chips.affordable(640, 1, self.DENOMS), 640)
        self.assertEqual(chips.affordable(630, 1, self.DENOMS), 630)

    def test_too_small_for_any_chip_returns_zero(self):
        self.assertEqual(chips.affordable(5, 1, self.DENOMS), 0)

    def test_the_order_marks_all_in_when_balance_caps_the_ladder(self):
        controller = BaccaratController(
            pathlib.Path(tempfile.mkdtemp()) / "s.json",
            type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.config["bet_stake"] = 20.0
        controller.config["bet_progression"] = "win_repeat_loss_double"
        controller.config["bet_max_stake"] = 0.0
        controller.bet_config = {"side": "B", "stake": 20.0}
        row = {"key": "id:t1", "label": "Bàn", "letters": road(4), "ts": time.time(),
               "table_id": "t1", "stable": 3}
        controller.tables = {"id:t1": row}
        with controller.lock:
            controller.sim.bind(row)
            controller.sim.start(row)
        # Đang thua ở 320, ví còn 636 → lệnh muốn 640, phải all-in.
        controller.sim.progress_stake = 320.0
        controller.sim.last_outcome = "loss"
        controller.sim.money.balance = 636.0
        order = controller._resolve_order()
        self.assertEqual(order["stake"], 636.0, "trần theo số dư")
        self.assertTrue(order["all_in"], "phải bật chế độ all-in cho tay này")

    def test_a_config_cap_does_not_trigger_all_in(self):
        controller = BaccaratController(
            pathlib.Path(tempfile.mkdtemp()) / "s.json",
            type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        controller.config["bet_stake"] = 20.0
        controller.config["bet_progression"] = "win_repeat_loss_double"
        controller.config["bet_max_stake"] = 100.0
        controller.bet_config = {"side": "B", "stake": 20.0}
        row = {"key": "id:t1", "label": "Bàn", "letters": road(4), "ts": time.time(),
               "table_id": "t1", "stable": 3}
        controller.tables = {"id:t1": row}
        with controller.lock:
            controller.sim.bind(row)
            controller.sim.start(row)
        controller.sim.progress_stake = 80.0
        controller.sim.last_outcome = "loss"
        controller.sim.money.balance = 5000.0
        order = controller._resolve_order()
        self.assertEqual(order["stake"], 100.0, "trần cấu hình chặn trước")
        self.assertFalse(order["all_in"],
                         "bị trần cấu hình cắt thì không phải all-in")


if __name__ == "__main__":
    unittest.main(verbosity=2)
