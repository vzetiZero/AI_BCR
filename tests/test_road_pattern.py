# -*- coding: utf-8 -*-
"""Big Road + xác suất theo dạng cầu + chọn cửa có xác suất cao nhất."""

import pathlib
import tempfile
import time
import unittest

from aibcr.controllers.baccarat import BaccaratController
from aibcr.live import cards, predict, road
from aibcr.live.session import LiveSession


class BigRoadTests(unittest.TestCase):
    def test_columns_and_dragon_tail(self):
        result = road.big_road("BBBBBBBP")
        # 6 ô đầu đầy cột 1; ô B thứ 7 đi sang phải cùng hàng (đuôi rồng) rồi P mở cột mới.
        self.assertEqual(len(result["columns"]), 3)
        self.assertEqual([cell["row"] for cell in result["columns"][0]], [0, 1, 2, 3, 4, 5])
        self.assertEqual([(cell["side"], cell["row"]) for cell in result["columns"][1]],
                         [("B", 5)])
        self.assertEqual(result["columns"][2][0]["side"], "P")

    def test_new_side_opens_new_column(self):
        result = road.big_road("BPBP")
        self.assertEqual(len(result["columns"]), 4)
        self.assertEqual([col[0]["side"] for col in result["columns"]],
                         ["B", "P", "B", "P"])

    def test_empty_and_junk_are_ignored(self):
        self.assertEqual(road.big_road("")["cells"], 0)
        self.assertEqual(road.big_road("BxP?T")["cells"], 2)
        self.assertEqual(road.last_streak("")["length"], 0)
        self.assertEqual(road.last_streak("BPPPT"), {"side": "P", "length": 3})

    def test_tie_is_a_marker_not_a_new_cell(self):
        """Big Road thật: hoà không mở cột mới mà là vạch trên ô của vệt đang chạy."""
        result = road.big_road("BTTB")
        self.assertEqual(len(result["columns"]), 1)
        self.assertEqual(result["cells"], 2)
        self.assertEqual(result["columns"][0][0]["ties"], 2)
        self.assertEqual([cell["row"] for cell in result["columns"][0]], [0, 1])
        leading = road.big_road("TTB")
        self.assertEqual(leading["cells"], 1)
        self.assertEqual(leading["leading_ties"], 2)
        self.assertEqual(leading["columns"][0][0]["ties"], 2)


class PatternTests(unittest.TestCase):
    def test_counts_and_streak(self):
        stats = predict.pattern_stats("BBPPPB")
        self.assertEqual(stats["total"], 6)
        self.assertEqual(stats["counts"], {"P": 3, "B": 3, "T": 0})
        self.assertEqual(stats["streak"], {"side": "B", "length": 1})
        self.assertAlmostEqual(sum(stats["shares"].values()), 1.0)

    def test_ties_do_not_break_the_visible_streak(self):
        """Big Road bỏ qua hoà: 3 tay B rồi hoà vẫn là chuỗi B 3 tay, không phải T."""
        stats = predict.pattern_stats("BBBT")
        self.assertEqual(stats["counts"]["T"], 1)
        self.assertEqual(stats["streak"], {"side": "B", "length": 3})

    def test_requires_enough_samples(self):
        stats = predict.pattern_stats("PB")
        self.assertIsNone(stats["probabilities"])

    def test_pattern_probabilities_when_history_repeats(self):
        # 10 chuỗi "PP" rồi đổi sang B: đủ mẫu cho bucket P dài 2.
        history = "PPB" * 10 + "PP"
        stats = predict.pattern_stats(history)
        self.assertEqual(stats["bucket"]["samples"], 10)
        self.assertIsNotNone(stats["probabilities"])
        self.assertAlmostEqual(stats["probabilities"]["B"], 1.0)

    def test_predict_exposes_top_side_and_normalised_probabilities(self):
        shoe = cards.Shoe(8)
        result = predict.predict(shoe, "PPBPBPPB", trials=800, seed=3)
        self.assertTrue(result["ok"])
        total = sum(result["probabilities"].values())
        self.assertAlmostEqual(total, 1.0, places=6)
        key = {"P": "p_player", "B": "p_banker", "T": "p_tie"}
        self.assertIn(result["top_side"], ("P", "B", "T"))
        self.assertAlmostEqual(result["top_probability"],
                               result["probabilities"][key[result["top_side"]]])
        self.assertIn("Cầu", predict.pattern_note(result["patterns"],
                                                  result["top_side"]))


class LiveSnapshotTests(unittest.TestCase):
    def test_snapshot_has_hand_history_and_road(self):
        session = LiveSession(trials=400)
        session.on_round({"issue_id": "a", "table": "WM 101_505", "winner": "B",
                          "player_cards": [1, 2], "banker_cards": [3, 4],
                          "round": 27})
        view = session.snapshot()
        self.assertEqual(view["history"], "B")
        self.assertEqual(view["hand"], 27)
        self.assertEqual(view["road"]["cells"], 1)
        self.assertEqual(view["road"]["columns"][0][0]["side"], "B")
        self.assertIn("pattern_note", view["prediction"])

    def test_state_updates_the_real_hand_number_and_notifies(self):
        """gameNoRound (protocol 21) là số tay thật: UI phải nhận ngay, không chờ ván mới."""
        session = LiveSession(trials=200)
        notified = []
        session.on_prediction = notified.append
        session.on_state({"table": "WM 101_505", "state": "BETTING", "round": 27,
                          "time_remains": 12.0})
        self.assertEqual(session.snapshot()["hand"], 27)
        self.assertEqual(session.snapshot()["state"], "BETTING")
        self.assertTrue(notified)
        # Tick betMilliSecond không làm bắn lại sự kiện (tránh spam UI mỗi giây).
        count = len(notified)
        session.on_state({"table": "WM 101_505", "state": "BETTING", "round": 27,
                          "time_remains": 11.0})
        self.assertEqual(len(notified), count)
        session.on_state({"table": "WM 101_505", "state": "SHOW_RESULT", "round": 27})
        self.assertEqual(len(notified), count + 1)


    def test_seed_rows_only_predict_once_at_the_end(self):
        """Nạp 30 ván cũ không được chạy Monte Carlo 30 lần (làm đứng UI)."""
        session = LiveSession(trials=100)
        notified = []
        session.on_prediction = notified.append
        for index in range(3):
            session.on_round({
                "issue_id": "seed:%d" % index, "table": "WM 101_505", "winner": "B",
                "player_cards": [1, 2], "banker_cards": [3, 4], "round": 24 + index,
                "seed": True, "seed_last": index == 2})
        self.assertEqual(session.snapshot()["history"], "BBB")
        self.assertEqual(session.snapshot()["hand"], 26)
        self.assertEqual(len(notified), 1)
        self.assertIsNotNone(session.snapshot()["prediction"])


class WmReaderHandTests(unittest.TestCase):
    def test_result_and_state_carry_the_round_number(self):
        from aibcr.adapters import wm
        from aibcr.live.wm_reader import WMReader

        rounds, states = [], []
        reader = WMReader(on_round=rounds.append, on_state=states.append, port=1)
        reader.open_key = wm.table_key(101, 508)
        reader.handle({"protocol": 21, "data": {
            "gameID": 101, "groupID": 508, "gameNo": 114798135, "gameNoRound": 27,
            "betMilliSecond": 19000, "tableDtExtend": {"tableName": "Bàn 1"}}})
        reader.handle({"protocol": 25, "data": {
            "gameID": 101, "groupID": 508, "result": 34,
            "playerScore": 3, "bankerScore": 2,
            "dtCard": {"1": {"cardID": 53}, "2": {"cardID": 72}, "3": {"cardID": 62},
                       "4": {"cardID": 11}, "5": {"cardID": 61}, "6": {"cardID": 62}}}})
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["winner"], "P")
        self.assertEqual(rounds[0]["round"], 27, "protocol 25 phải kế thừa số tay từ 21")
        self.assertEqual(states[-1]["round"], 27)
        self.assertEqual(states[-1]["state"], "SHOW_RESULT")


class HighestProbabilityBetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_controller(self):
        class Emitter:
            def event(self, name, data):
                pass

        controller = BaccaratController(
            str(pathlib.Path(self.tmp.name) / "state.json"), Emitter())
        self.addCleanup(controller.shutdown)
        return controller

    def view(self):
        return {
            "running": True, "hand": 12, "rounds": 6, "table": "WM test",
            "history": "BBPBBP",
            "prediction": {"ok": True, "playable": False, "best_side": None,
                           "top_side": "B", "top_probability": 0.46,
                           "probabilities": {"p_player": 0.44, "p_banker": 0.46,
                                             "p_tie": 0.10}},
            "analysis": {"verdict": "BỎ TAY", "focus_side": "T"},
        }

    def ready(self, controller):
        row = {"key": "id:t1", "label": "WM test", "letters": "BBP",
               "ts": time.time(), "table_id": "t1", "stable": 3}
        controller.tables = {row["key"]: row}
        controller.sim.bind(row)
        controller.sim.start(row)
        controller.sim.entry_waiting = False
        controller.tool_running = True
        return row

    def test_auto_side_is_highest_probability_even_when_ev_is_negative(self):
        controller = self.make_controller()

        class FakeLive:
            def snapshot(self):
                return self_outer.view()

        self_outer = self
        controller.live_session = FakeLive()
        controller.dispatch("bet.config", {"side": "AUTO", "stake": 60})
        self.ready(controller)
        placed = []
        controller._place_armed_bet = lambda order: placed.append(dict(order))
        result, error = controller.dispatch("bet.place", {})
        self.assertIsNone(error)
        self.assertTrue(result["ok"], result)
        self.assertEqual(placed[0]["side"], "B")
        self.assertEqual(placed[0]["stake"], 60.0)
        self.assertIn("46.0%", placed[0]["reason"])

    def test_auto_side_falls_back_to_probabilities_when_top_side_missing(self):
        controller = self.make_controller()

        class FakeLive:
            def snapshot(self):
                view = self_outer.view()
                view["prediction"].pop("top_side")
                view["prediction"].pop("top_probability")
                view["prediction"]["probabilities"] = {"p_player": 0.39,
                                                       "p_banker": 0.51,
                                                       "p_tie": 0.10}
                return view

        self_outer = self
        controller.live_session = FakeLive()
        controller.dispatch("bet.config", {"side": "AUTO", "stake": 10})
        self.ready(controller)
        placed = []
        controller._place_armed_bet = lambda order: placed.append(dict(order))
        result, error = controller.dispatch("bet.place", {})
        self.assertIsNone(error)
        self.assertTrue(result["ok"], result)
        self.assertEqual(placed[0]["side"], "B")


if __name__ == "__main__":
    unittest.main(verbosity=2)
