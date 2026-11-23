# -*- coding: utf-8 -*-
"""Đặt cược WM: mệnh giá khay, khung lệnh cược, chọn socket và điều kiện cửa sổ cược.

Các giá trị dưới đây lấy từ bàn thật: khay chip WM in ``10/20/50/100/500/1k`` (nghìn
đồng), lệnh cược đi qua WebSocket ``protocol 22`` với ``betArea`` 1/2/3 = B/P/T.
"""

import unittest

from aibcr.adapters import wm_bet
from aibcr.adapters import wm_ws


class ChipValueTests(unittest.TestCase):
    def test_plain_and_k_suffix(self):
        self.assertEqual(wm_bet.chip_value("10"), 10)
        self.assertEqual(wm_bet.chip_value("100"), 100)
        self.assertEqual(wm_bet.chip_value("1k"), 1000)
        self.assertEqual(wm_bet.chip_value("2.5k"), 2500)

    def test_garbage_is_zero(self):
        for value in ("", None, "abc", "-"):
            self.assertEqual(wm_bet.chip_value(value), 0)

    def test_denominations_are_sorted_and_unique(self):
        chips = [{"key": "100"}, {"key": "10"}, {"key": "1k"}, {"key": "10"}]
        self.assertEqual(wm_bet.parse_denominations(chips), [10, 100, 1000])


class DecomposeTests(unittest.TestCase):
    """Không bao giờ đặt thiếu: chia không khít thì phải từ chối."""

    def test_exact_split(self):
        plan = wm_bet.CHIPS.decompose(30, unit=1, denominations=[10, 20, 50, 100])
        self.assertTrue(plan["exact"])
        self.assertEqual(sum(plan["chips"]), 30)

    def test_unreachable_amount_is_rejected(self):
        plan = wm_bet.CHIPS.decompose(25, unit=1, denominations=[10, 20])
        self.assertFalse(plan["exact"])

    def test_smaller_than_smallest_chip(self):
        plan = wm_bet.CHIPS.decompose(5, unit=1, denominations=[10, 20])
        self.assertFalse(plan["exact"])


class BetFrameTests(unittest.TestCase):
    def test_frame_matches_captured_traffic(self):
        frame = wm_ws.bet_frame("P", 10, 12215169, 17, serial=1)
        self.assertEqual(frame["protocol"], 22)
        data = frame["data"]
        bet = data["betArr"][0]
        self.assertEqual(bet["betArea"], 2)          # 2 = Player
        self.assertEqual(bet["addBetMoney"], 10)
        self.assertEqual(data["gameNo"], 12215169)
        self.assertEqual(data["gameNoRound"], 17)
        self.assertEqual(data["betSerialNumber"], 1)
        self.assertEqual(data["commission"], 0)
        self.assertEqual(data["remark"], "1,0")

    def test_area_mapping(self):
        self.assertEqual(wm_ws.BET_AREA, {"B": 1, "P": 2, "T": 3})
        self.assertEqual(wm_ws.bet_frame("B", 20, 1, 1)["data"]["betArr"][0]["betArea"], 1)
        self.assertEqual(wm_ws.bet_frame("T", 20, 1, 1)["data"]["betArr"][0]["betArea"], 3)


class CurrentTableTests(unittest.TestCase):
    """Chọn đúng socket của bàn đang mở, không gửi lệnh sang bàn khác."""

    def _state(self, frames, group=1352, states=None, scopes=None):
        return {"recent21": frames, "open_group": group,
                "states": states if states is not None else [1, 1, 1],
                "scopes": scopes if scopes is not None else {0: "iframe", 1: "top",
                                                            2: "iframe"}}

    def test_picks_matching_group(self):
        frames = [
            {"socket": 0, "at": 1000, "data": {"groupID": 613, "gameNo": 1}},
            {"socket": 2, "at": 2000, "data": {"groupID": 1352, "gameNo": 9}},
            {"socket": 0, "at": 3000, "data": {"groupID": 632, "gameNo": 4}},
        ]
        index, data, at = wm_ws.current_table(self._state(frames))
        self.assertEqual(index, 2)
        self.assertEqual(data["gameNo"], 9)
        self.assertEqual(at, 2000)

    def test_newest_frame_of_the_table_wins(self):
        frames = [
            {"socket": 2, "at": 1000, "data": {"groupID": 1352, "gameNo": 7}},
            {"socket": 2, "at": 5000, "data": {"groupID": 1352, "gameNo": 8}},
        ]
        self.assertEqual(wm_ws.current_table(self._state(frames))[1]["gameNo"], 8)

    def test_no_match_means_no_bet(self):
        frames = [{"socket": 0, "at": 1000, "data": {"groupID": 613}}]
        self.assertIsNone(wm_ws.current_table(self._state(frames)))

    def test_closed_socket_is_ignored(self):
        frames = [{"socket": 2, "at": 9000, "data": {"groupID": 1352}}]
        self.assertIsNone(wm_ws.current_table(self._state(frames, states=[1, 1, 3])))

    def test_prefers_table_iframe_socket(self):
        """Socket sảnh nhận được dữ liệu bàn nhưng server từ chối lệnh cược từ đó."""
        frames = [
            {"socket": 1, "at": 9000, "data": {"groupID": 1352, "gameNo": 11}},  # top
            {"socket": 2, "at": 5000, "data": {"groupID": 1352, "gameNo": 11}},  # iframe
        ]
        index, _data, _at = wm_ws.current_table(self._state(frames))
        self.assertEqual(index, 2)

    def test_falls_back_to_any_socket_when_group_unknown(self):
        frames = [{"socket": 1, "at": 9000, "data": {"groupID": 613}}]
        state = self._state(frames, group=None)
        self.assertEqual(wm_ws.current_table(state)[0], 1)

    def test_uses_embedded_wm_socket_when_no_same_origin_frame(self):
        """WM nhúng khác origin: socket bàn nằm ở khung nhúng, không có 'iframe'."""
        frames = [{"socket": 0, "at": 9000, "data": {"groupID": 1352, "gameNo": 3}}]
        state = {"recent21": frames, "open_group": 1352, "states": [1],
                 "scopes": {0: "oopif"}}
        index, data, _at = wm_ws.current_table(state)
        self.assertEqual(index, 0)
        self.assertEqual(data["gameNo"], 3)

    def test_same_origin_frame_socket_beats_embedded_one(self):
        frames = [
            {"socket": 0, "at": 9000, "data": {"groupID": 1352}},  # oopif
            {"socket": 1, "at": 1000, "data": {"groupID": 1352}},  # iframe bàn
        ]
        state = {"recent21": frames, "open_group": 1352, "states": [1, 1],
                 "scopes": {0: "oopif", 1: "iframe"}}
        self.assertEqual(wm_ws.current_table(state)[0], 1)


class MergeStatesTests(unittest.TestCase):
    """Gộp hook của tab cổng + target iframe khác origin, giữ đúng chỉ số socket."""

    def test_offsets_routes_and_scopes(self):
        page = {"installed": True, "urls": ["wss://portal/ws"], "states": [1],
                "scopes": {0: "top"}, "protocols": {0: 21},
                "recent21": [{"socket": 0, "at": 100, "data": {"groupID": 7}}],
                "sent": [], "errors": [], "open_group": 7}
        oopif = {"installed": True, "urls": ["wss://wm/a", "wss://wm/b"],
                 "states": [1, 3], "scopes": {0: "iframe", 1: "iframe"},
                 "protocols": {0: 21}, "recent21": [
                     {"socket": 1, "at": 50, "data": {"groupID": 7}}],
                 "sent": [], "errors": [], "open_group": 7}
        merged = wm_ws.merge_states([("oopif", oopif), ("page", page)])
        self.assertEqual(merged["urls"],
                         ["wss://wm/a", "wss://wm/b", "wss://portal/ws"])
        # Socket của khung nhúng được đánh dấu riêng, socket khung gốc giữ 'top'.
        self.assertEqual(merged["scopes"][0], "iframe")
        self.assertEqual(merged["scopes"][2], "top")
        self.assertEqual(merged["routes"], ["oopif", "oopif", "page"])
        self.assertEqual(merged["locals"], [0, 1, 0])
        # Chỉ số khung nhúng được dồn tiếp sau khung trước khi gộp.
        self.assertEqual([item["socket"] for item in merged["recent21"]], [1, 2])
        self.assertEqual(merged["open_group"], 7)
        self.assertTrue(merged["installed"])

    def test_closed_socket_states_survive(self):
        merged = wm_ws.merge_states([("page", {"states": [1, 3], "scopes": {}})])
        self.assertEqual(merged["states"], [1, 3])
        self.assertEqual(merged["scopes"][0], None)
        self.assertFalse(merged["installed"])


class SocketInstallJsTests(unittest.TestCase):
    def test_install_hook_covers_every_iframe(self):
        """Bàn có thể nằm ở iframe không tên iframe_101 → phải quét mọi iframe."""
        self.assertIn("querySelectorAll('iframe')", wm_ws.INSTALL_TABLE_JS)

    def test_reload_targets_are_reported_not_swallowed(self):
        self.assertIn("no_table_frame", wm_ws.RELOAD_TABLE_JS)
        self.assertIn("iframe_101", wm_ws.TABLE_FRAME_PICKER_JS)


class ClickableTests(unittest.TestCase):
    def test_requires_real_size(self):
        self.assertTrue(wm_bet.box_clickable({"w": 38, "h": 38}))
        self.assertFalse(wm_bet.box_clickable({"w": 0, "h": 38}))
        self.assertFalse(wm_bet.box_clickable(None))

    def test_uncovered_detects_overlay(self):
        self.assertTrue(wm_bet.uncovered({"hit": "chip_item mouse_pointer chip_b_10"}))
        self.assertTrue(wm_bet.uncovered({"hit": "playbetboxPlayer|playerbet_box"}))
        # Lớp phủ chia bài che khay chip: chưa phải lúc đặt cược.
        self.assertFalse(wm_bet.uncovered({"hit": "front bg poker_item"}))
        self.assertFalse(wm_bet.uncovered({}))


class ReadBetsFormatTests(unittest.TestCase):
    def test_spots_match_table_bets_contract(self):
        live = {"ok": True, "spots": {"B": {"amount": 10.0, "amount_vnd": 10.0, "chips": 2}},
                "total": 10.0, "total_units": 10.0, "denominations": [10], "unit": 1}
        from aibcr.models.live_sim import table_is_clear
        self.assertFalse(table_is_clear(live))
        self.assertTrue(table_is_clear({"ok": True, "spots": {}}))


class _FakeSocket:
    def __init__(self, ready):
        self._ready = ready
        self.calls = []

    def is_alive(self):
        return True

    def ready(self):
        return self._ready

    def place(self, side, amount):
        self.calls.append((side, amount))
        return {"ok": True}


class _FakeSession:
    def __init__(self, socket):
        self.wm_socket = socket


class PlaceIsWebSocketOnlyTests(unittest.TestCase):
    """Không còn đường chuột: thiếu socket thì báo lỗi rõ ràng, không âm thầm bấm chip."""

    PHASE = {"ok": True, "chips": [{"key": "10", "w": 38, "h": 38,
                                    "hit": "chip_item chip_b_10"}]}

    def _bettor(self, socket):
        bettor = wm_bet.WMBet(_FakeSession(socket))
        bettor.wait_phase = lambda *a, **k: self.PHASE
        bettor.read_bets = lambda: {"ok": True, "spots": {}, "total": 0.0,
                                    "denominations": [10]}
        return bettor

    def test_missing_socket_is_an_explicit_error(self):
        result = self._bettor(_FakeSocket(ready=False)).place("P", 10, wait_seconds=1)
        self.assertFalse(result["ok"])
        self.assertIn("WebSocket", result["error"])
        self.assertEqual(result["via"], "ws")

    def test_confirm_false_reports_that_only_websocket_exists(self):
        result = self._bettor(_FakeSocket(ready=True)).place("P", 10, confirm=False,
                                                             wait_seconds=1)
        self.assertFalse(result["ok"])
        self.assertIn("WebSocket", result["error"])

    def test_unreachable_amount_never_goes_to_the_site(self):
        socket = _FakeSocket(ready=True)
        bettor = self._bettor(socket)
        bettor.wait_phase = lambda *a, **k: {"ok": True, "chips": [{"key": "100", "w": 38,
                                                                  "h": 38}]}
        result = bettor.place("P", 25, wait_seconds=1)
        self.assertFalse(result["ok"])
        self.assertEqual(socket.calls, [])


if __name__ == "__main__":
    unittest.main()
