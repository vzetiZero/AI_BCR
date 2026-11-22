# -*- coding: utf-8 -*-
"""Lịch sử tay cược bền vững: ghi nối thêm (JSONL) và xuất CSV/JSON/TXT.

Vì sao cần module riêng: ``LiveSimulation.log`` chỉ là bộ đệm RAM (và state file chỉ
giữ 500 dòng cuối), nên mở lại app là mất dữ liệu cũ. Ở đây mỗi tay đã kết toán được
ghi nối thêm vào một file JSONL **ngoài phiên**: ``session.reset``/``session.new``
không đụng tới, chỉ nút "Reset lịch sử" mới xoá (và luôn lưu bản sao trước khi xoá).

Module này chỉ xử lý dữ liệu thuần (không Chrome, không UI) để controller gọi.
"""

import json
import os
import pathlib
import time

# Thứ tự cột dùng chung cho bảng UI và file xuất. ``table_key`` đi kèm để khoá chống
# trùng còn ổn định sau khi nạp lại file (``table`` là tên hiển thị, có thể trùng nhau),
# còn ``armed_at`` PHẢI được lưu: ``row_key`` dựa vào nó, thiếu nó thì lần nạp sau khoá
# đổi và cùng một tay bị ghi hai lần.
HISTORY_FIELDS = ("time", "armed_at", "at", "table", "table_key", "hand", "bet",
                  "result", "stake", "delta", "outcome", "balance_after", "source")

EXPORT_FORMATS = ("csv", "json", "txt")
FORMAT_EXTENSION = {"csv": "csv", "json": "json", "txt": "txt"}


def row_key(row):
    """Khoá bền để chống ghi trùng khi khôi phục state hoặc gọi lưu nhiều lần.

    ``armed_at`` là mốc chốt lệnh (một lần mỗi tay) nên cặp với bàn + số tay là duy
    nhất. Bản ghi cũ không có ``armed_at`` thì dùng ``time``.
    """
    stamp = row.get("armed_at") or row.get("time") or 0
    return "%.6f|%s|%s" % (float(stamp or 0), row.get("table_key") or "",
                           row.get("hand") or 0)


def normalize_row(row):
    """Trả dict chỉ gồm ``HISTORY_FIELDS``, điền ``at`` nếu thiếu."""
    out = {}
    for key in HISTORY_FIELDS:
        value = row.get(key)
        if key == "hand":
            try:
                value = int(value or 0)
            except (TypeError, ValueError):
                value = 0
        elif key in ("stake", "delta", "balance_after", "armed_at"):
            try:
                value = float(value or 0.0)
            except (TypeError, ValueError):
                value = 0.0
        elif key == "time":
            try:
                value = float(value or 0.0)
            except (TypeError, ValueError):
                value = 0.0
        else:
            value = "" if value is None else str(value)
        out[key] = value
    if not out["at"]:
        out["at"] = time.strftime("%H:%M:%S", time.localtime(out["time"] or time.time()))
    return out


def summarize(rows):
    """Thống kê tích luỹ của lịch sử: số tay tính tiền, thắng/thua/hoà, lãi/lỗ."""
    hands = wins = losses = pushes = skipped = 0
    staked = profit = 0.0
    for row in rows:
        outcome = str(row.get("outcome") or "")
        if outcome == "skip":
            skipped += 1
            continue
        hands += 1
        staked += float(row.get("stake") or 0)
        profit += float(row.get("delta") or 0)
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        elif outcome == "push":
            pushes += 1
        else:
            skipped += 1
    decisive = wins + losses
    return {
        "rows": len(rows),
        "hands": hands,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "skipped": skipped,
        "staked": round(staked, 2),
        "profit": round(profit, 2),
        "win_rate": round(wins / decisive * 100, 2) if decisive else 0.0,
    }


def _csv_cell(value):
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"%s"' % text.replace('"', '""')
    return text


def to_csv(rows):
    """CSV có BOM để Excel đọc đúng tiếng Việt."""
    lines = [",".join(HISTORY_FIELDS)]
    lines += [",".join(_csv_cell(row.get(key)) for key in HISTORY_FIELDS) for row in rows]
    return "\ufeff" + "\r\n".join(lines) + "\r\n"


def to_json(rows):
    return json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2)


def _txt_money(value):
    return "{:,.2f}".format(float(value or 0.0)).replace(",", "_").replace(".", ",").replace("_", ".")


def to_txt(rows):
    """Báo cáo dạng cột cố định, dễ đọc/đối chiếu bằng mắt hoặc in ra."""
    stats = summarize(rows)
    lines = [
        "LỊCH SỬ TAY CƯỢC AI_BCR",
        "Xuất lúc: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "Tổng tay: %d · thắng %d · thua %d · hoà %d · bỏ qua %d" % (
            stats["hands"], stats["wins"], stats["losses"], stats["pushes"],
            stats["skipped"]),
        "Tổng cược: %s · lãi/lỗ: %s · tỉ lệ thắng: %.2f%%" % (
            _txt_money(stats["staked"]), _txt_money(stats["profit"]),
            stats["win_rate"]),
        "",
        "%-10s %-22s %6s %-7s %-6s %14s %14s %16s" % (
            "GIỜ", "BÀN", "TAY", "CƯỢC", "KQ", "TIỀN", "LÃI/LỖ", "VỐN"),
        "-" * 100,
    ]
    for row in rows:
        lines.append("%-10s %-22s %6s %-7s %-6s %14s %14s %16s" % (
            str(row.get("at") or "")[:10],
            str(row.get("table") or "")[:22],
            row.get("hand") or "",
            row.get("bet") or "",
            row.get("result") or "",
            _txt_money(row.get("stake")),
            _txt_money(row.get("delta")),
            _txt_money(row.get("balance_after")),
        ))
    return "\n".join(lines) + "\n"


RENDERERS = {"csv": to_csv, "json": to_json, "txt": to_txt}


def export_text(rows, fmt="csv"):
    """Sinh nội dung file xuất. ``fmt`` sai thì dùng CSV."""
    renderer = RENDERERS.get(str(fmt or "").strip().lower(), to_csv)
    return renderer(list(rows))


def export_name(fmt="csv", stamp=None):
    ext = FORMAT_EXTENSION.get(str(fmt or "").strip().lower(), "csv")
    moment = stamp or time.strftime("%Y%m%d-%H%M%S")
    return "aibcr-history-%s.%s" % (moment, ext)


class HistoryStore:
    """File JSONL nối thêm, dedupe theo ``row_key``.

    Không tự xoá gì: chỉ ``clear()`` mới làm rỗng, và nó **luôn** chép bản sao sang
    file archive trước khi xoá để người dùng còn dữ liệu đối chiếu.
    """

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self._rows = []
        self._keys = set()
        self._stats = None
        self.load()

    # ---------------------------------------------------------------- đọc/ghi
    def load(self):
        """Nạp toàn bộ file; file hỏng/không có thì coi như rỗng, không ném lỗi."""
        self._rows = []
        self._keys = set()
        self._stats = None
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            if not isinstance(raw, dict):
                continue
            row = normalize_row(raw)
            key = row_key(raw)
            if key in self._keys:
                continue
            self._keys.add(key)
            self._rows.append(row)
        return len(self._rows)

    def append(self, rows):
        """Ghi thêm những bản ghi chưa có. Trả số bản ghi mới đã thêm."""
        fresh = []
        for raw in rows or ():
            if not isinstance(raw, dict):
                continue
            key = row_key(raw)
            if key in self._keys:
                continue
            self._keys.add(key)
            fresh.append(normalize_row(raw))
        if not fresh:
            return 0
        self._rows.extend(fresh)
        self._stats = None      # tổng hợp lại ở lần ``stats()`` kế tiếp
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in fresh:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return len(fresh)

    # ------------------------------------------------------------------- đọc
    def rows(self, limit=None):
        if not limit or limit <= 0:
            return list(self._rows)
        return list(self._rows[-limit:])

    def __len__(self):
        return len(self._rows)

    def stats(self):
        if self._stats is None:
            self._stats = summarize(self._rows)
        return dict(self._stats)

    # ------------------------------------------------------------------ xoá
    def archive(self, stamp=None):
        """Chép file hiện tại sang file archive và trả đường dẫn (None nếu rỗng)."""
        if not self._rows:
            return None
        moment = stamp or time.strftime("%Y%m%d-%H%M%S")
        archive_path = self.path.with_name("aibcr-history-%s.jsonl" % moment)
        try:
            if self.path.exists():
                archive_path.write_bytes(self.path.read_bytes())
        except OSError:
            return None
        return archive_path

    def clear(self, stamp=None):
        """Lưu bản sao rồi làm rỗng. Trả ``(số dòng đã xoá, đường dẫn archive)``."""
        removed = len(self._rows)
        archive_path = self.archive(stamp)
        self._rows = []
        self._keys = set()
        self._stats = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")
        except OSError:
            pass
        return removed, archive_path
