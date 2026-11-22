# -*- coding: utf-8 -*-
"""Kiểm thử lịch sử bền vững: file JSONL, dedupe, xuất CSV/JSON/TXT, reset.

Yêu cầu người dùng:
    - Lịch sử lưu từ bây giờ về sau, KHÔNG mất khi mở lại app.
    - Reset phiên / tạo phiên mới KHÔNG xoá lịch sử.
    - Nút reset có, và trước khi xoá phải lưu bản sao để còn đối chiếu.
    - Xuất được CSV/JSON/TXT.
"""

import json
import pathlib
import tempfile
import unittest

from aibcr.controllers.baccarat import BaccaratController
from aibcr.models import history as H


def sample_row(**overrides):
    row = {"time": 1700000000.0, "armed_at": 1700000001.0, "table": "Bàn A",
           "table_key": "id:a", "hand": 41, "bet": "B", "result": "P", "stake": 40.0,
           "delta": -40.0, "outcome": "loss", "balance_after": 1960.0,
           "source": "live-driver"}
    row.update(overrides)
    return row


def store():
    folder = pathlib.Path(tempfile.mkdtemp())
    return H.HistoryStore(folder / "aibcr_history.jsonl")


class HistoryStoreTests(unittest.TestCase):
    def test_rows_survive_a_restart(self):
        first = store()
        self.assertEqual(first.append([sample_row()]), 1)
        # "Mở lại app": tạo store mới trên cùng file.
        second = H.HistoryStore(first.path)
        rows = second.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["table"], "Bàn A")
        self.assertEqual(rows[0]["result"], "P")
        self.assertEqual(rows[0]["outcome"], "loss")

    def test_appending_keeps_older_hands(self):
        book = store()
        for hand in (40, 41, 42):
            book.append([sample_row(hand=hand, armed_at=1700000000.0 + hand)])
        reloaded = H.HistoryStore(book.path)
        self.assertEqual([row["hand"] for row in reloaded.rows()], [40, 41, 42])

    def test_duplicate_rows_are_ignored(self):
        book = store()
        book.append([sample_row()])
        self.assertEqual(book.append([sample_row()]), 0)
        self.assertEqual(book.append([dict(sample_row(), balance_after=1.0)]), 0)
        self.assertEqual(len(H.HistoryStore(book.path)), 1)

    def test_same_hand_on_different_tables_is_not_a_duplicate(self):
        book = store()
        book.append([sample_row()])
        book.append([sample_row(table="Bàn B", table_key="id:b")])
        self.assertEqual(len(book), 2)

    def test_a_corrupt_line_does_not_break_loading(self):
        book = store()
        book.append([sample_row()])
        with book.path.open("a", encoding="utf-8") as handle:
            handle.write("{khong-phai-json}\n")
            handle.write(json.dumps(sample_row(hand=99, armed_at=99.0)) + "\n")
        reloaded = H.HistoryStore(book.path)
        self.assertEqual(len(reloaded), 2, "dòng hỏng bị bỏ, dòng tốt vẫn nạp")

    def test_at_is_filled_when_missing(self):
        book = store()
        book.append([sample_row(at="")])
        self.assertRegex(book.rows()[0]["at"], r"^\d\d:\d\d:\d\d$")

    def test_stats_are_cumulative(self):
        book = store()
        book.append([
            sample_row(hand=1, armed_at=1.0, outcome="win", delta=38.0, stake=40.0),
            sample_row(hand=2, armed_at=2.0, outcome="loss", delta=-40.0),
            sample_row(hand=3, armed_at=3.0, outcome="push", delta=0.0),
            sample_row(hand=4, armed_at=4.0, outcome="not_placed", delta=0.0, stake=0.0),
        ])
        stats = book.stats()
        self.assertEqual(stats["hands"], 4)
        self.assertEqual((stats["wins"], stats["losses"], stats["pushes"]), (1, 1, 1))
        self.assertEqual(stats["profit"], -2.0)
        self.assertEqual(stats["win_rate"], 50.0)

    def test_export_csv_has_bom_and_every_row(self):
        book = store()
        book.append([sample_row(), sample_row(hand=42, armed_at=42.0)])
        text = H.export_text(book.rows(), "csv")
        self.assertTrue(text.startswith("\ufeff"), "BOM để Excel đọc đúng tiếng Việt")
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(lines), 3, "1 header + 2 dòng")
        self.assertIn("time,armed_at,at,table,table_key", lines[0])

    def test_export_csv_quotes_commas(self):
        book = store()
        book.append([sample_row(table="Bàn A, tầng 2")])
        text = H.export_text(book.rows(), "csv")
        self.assertIn('"Bàn A, tầng 2"', text)

    def test_export_json_round_trips(self):
        book = store()
        book.append([sample_row()])
        text = H.export_text(book.rows(), "json")
        parsed = json.loads(text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["hand"], 41)

    def test_export_txt_has_a_header_and_stats(self):
        book = store()
        book.append([sample_row()])
        text = H.export_text(book.rows(), "txt")
        self.assertIn("LỊCH SỬ TAY CƯỢC AI_BCR", text)
        self.assertIn("Tổng tay: 1", text)
        self.assertIn("Bàn A", text)

    def test_export_names_carry_the_format(self):
        self.assertTrue(H.export_name("csv").endswith(".csv"))
        self.assertTrue(H.export_name("json").endswith(".json"))
        self.assertTrue(H.export_name("txt").endswith(".txt"))
        self.assertTrue(H.export_name("khong-co").endswith(".csv"))

    def test_clear_archives_before_erasing(self):
        book = store()
        book.append([sample_row(), sample_row(hand=42, armed_at=42.0)])
        removed, archive = book.clear(stamp="20260101-120000")
        self.assertEqual(removed, 2)
        self.assertIsNotNone(archive)
        self.assertTrue(archive.exists())
        self.assertEqual(len(json.loads("[]")), 0)
        archived = [line for line in archive.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        self.assertEqual(len(archived), 2, "bản sao còn đủ 2 tay để đối chiếu")
        self.assertEqual(len(H.HistoryStore(book.path)), 0, "file chính đã rỗng")

    def test_clear_on_empty_store_is_harmless(self):
        book = store()
        removed, archive = book.clear()
        self.assertEqual(removed, 0)
        self.assertIsNone(archive)


class ControllerHistoryTests(unittest.TestCase):
    def make(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        controller = BaccaratController(
            folder / "state.json", type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(controller.shutdown)
        return controller

    def test_history_file_lives_beside_the_state_file(self):
        controller = self.make()
        self.assertEqual(controller.history_path.name, "aibcr_history.jsonl")
        self.assertEqual(controller.history_path.parent, controller.state_path.parent)

    def test_saving_state_persists_hands_to_the_history_file(self):
        controller = self.make()
        controller.sim.log = [sample_row(), sample_row(hand=42, armed_at=42.0)]
        controller._save_state()
        reloaded = H.HistoryStore(controller.history_path)
        self.assertEqual([row["hand"] for row in reloaded.rows()], [41, 42])

    def test_history_survives_a_new_controller_on_the_same_state_path(self):
        first = self.make()
        first.sim.log = [sample_row()]
        first._save_state()
        second = BaccaratController(
            first.state_path, type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(second.shutdown)
        self.assertEqual(len(second.history), 1)
        snapshot = second.snapshot()
        self.assertEqual(len(snapshot["history"]), 1)
        self.assertEqual(snapshot["history_stats"]["rows"], 1)

    def test_session_reset_keeps_the_history_file(self):
        controller = self.make()
        controller.sim.log = [sample_row()]
        controller._save_state()
        controller.dispatch("session.reset", {})
        self.assertEqual(len(controller.history), 1, "reset phiên không xoá lịch sử")

    def test_history_reset_dispatch_clears_and_reports_archive(self):
        controller = self.make()
        controller.sim.log = [sample_row()]
        controller._save_state()
        reply, error = controller.dispatch("history.reset", {})
        self.assertIsNone(error)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["removed"], 1)
        self.assertTrue(reply["archive"].endswith(".jsonl"))
        self.assertEqual(len(controller.history), 0)
        self.assertTrue((controller.state_path.parent / reply["archive"]).exists())

    def test_history_export_dispatch_returns_file_content(self):
        controller = self.make()
        controller.sim.log = [sample_row()]
        controller._save_state()
        reply, error = controller.dispatch("history.export", {"format": "csv"})
        self.assertIsNone(error)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["rows"], 1)
        self.assertTrue(reply["name"].endswith(".csv"))
        self.assertIn("Bàn A", reply["text"])

    def test_history_export_rejects_unknown_format(self):
        controller = self.make()
        reply, error = controller.dispatch("history.export", {"format": "xml"})
        self.assertIsNone(error)
        self.assertFalse(reply["ok"])
        self.assertIn("csv", reply["error"])

    def test_history_export_on_empty_history_is_a_clear_error(self):
        controller = self.make()
        reply, error = controller.dispatch("history.export", {"format": "json"})
        self.assertIsNone(error)
        self.assertFalse(reply["ok"])
        self.assertIn("Chưa có lịch sử", reply["error"])

    def test_snapshot_carries_cumulative_history_not_only_this_session(self):
        first = self.make()
        first.sim.log = [sample_row(hand=1, armed_at=1.0),
                         sample_row(hand=2, armed_at=2.0)]
        first._save_state()
        second = BaccaratController(
            first.state_path, type("E", (), {"event": lambda self, n, d: None})())
        self.addCleanup(second.shutdown)
        second.sim.log = []
        snapshot = second.snapshot()
        self.assertEqual([row["hand"] for row in snapshot["history"]], [1, 2])
        self.assertEqual(snapshot["history_file"], "aibcr_history.jsonl")

    def test_repeated_saves_do_not_duplicate_rows(self):
        controller = self.make()
        controller.sim.log = [sample_row()]
        for _ in range(4):
            controller._save_state()
        self.assertEqual(len(H.HistoryStore(controller.history_path)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
