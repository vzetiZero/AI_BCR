# -*- coding: utf-8 -*-
"""Trạng thái một bàn Petalive dùng chung cho CLI và Electron.

Tách từ ``tools/bcr_live.py`` để hai luồng (terminal và UI) cùng dùng một cơ chế:

    khung WS -> decode -> on_round() -> Shoe -> predict.predict() -> snapshot()

AI được gọi **bất đồng bộ** trong thread daemon: vòng đọc WebSocket và vòng điều
phối của controller không bao giờ bị chặn bởi timeout của API.
"""

import math
import threading
import time

from aibcr.live import ai, cards, predict

RECENT_LIMIT = 40


def _num(value):
    """Số hữu hạn để Electron JSON không nhận NaN/Infinity."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class LiveSession:
    """Một bàn: shoe, lịch sử B/P/T, dự đoán gần nhất và nhận định AI."""

    def __init__(self, decks=8, trials=predict.DEFAULT_TRIALS,
                 markov_weight=predict.DEFAULT_MARKOV_WEIGHT, table="",
                 ai_mode="off", ai_url="", ai_model="", ai_key="", ai_every=5, ai_timeout=8,
                 on_prediction=None, on_status=None):
        self.shoe = cards.Shoe(decks)
        self.decks = int(decks)
        self.trials = int(trials)
        self.markov_weight = float(markov_weight)
        self.table = str(table or "")
        self.ai_mode = str(ai_mode or "off")
        self.ai_url = str(ai_url or "")
        self.ai_model = str(ai_model or "")
        # Chỉ nằm trong RAM; env BCR_AI_KEY là mặc định khi rỗng.
        self.ai_key = str(ai_key or "")
        self.ai_every = max(1, int(ai_every))
        self.ai_timeout = max(2, int(ai_timeout))
        self.on_prediction = on_prediction
        self.on_status = on_status

        self.history = ""
        self.rounds = []
        self.state = None
        self.prediction = None
        self.analysis = None
        self.issue_seen = None
        self.duplicates = 0
        self.ai_busy = False
        self.ai_error = ""
        self.running = False
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ cấu hình
    def configure(self, **kwargs):
        """Cập nhật cấu hình từ UI mà không tạo lại shoe/lịch sử."""
        allowed = ("decks", "trials", "markov_weight", "ai_mode", "ai_url",
                   "ai_model", "ai_key", "ai_every", "ai_timeout")
        with self.lock:
            for key in allowed:
                if key in kwargs and kwargs[key] is not None:
                    setattr(self, key, kwargs[key])
            self.ai_every = max(1, int(self.ai_every))
            self.ai_timeout = max(2, int(self.ai_timeout))

    def _ai_cfg(self):
        return {"mode": self.ai_mode, "url": self.ai_url, "model": self.ai_model,
                "key": self.ai_key, "timeout": self.ai_timeout}

    def _notify(self):
        if self.on_prediction is None:
            return
        try:
            self.on_prediction(self.snapshot())
        except Exception:
            pass

    def _status(self, text, level="info"):
        if self.on_status is None:
            return
        try:
            self.on_status(text, level)
        except Exception:
            pass

    # ------------------------------------------------------------------ ván mới
    def on_round(self, data):
        if self.table and data.get("table") != self.table:
            return
        with self.lock:
            # issueId phát lại khi reconnect không được cộng lá vào shoe lần thứ hai.
            if data.get("issue_id") and data["issue_id"] == self.issue_seen:
                self.duplicates += 1
                return
            self.issue_seen = data.get("issue_id")
            self.table = self.table or data.get("table") or ""
            verdict = predict.advance_round(self.shoe, data["player_cards"],
                                            data["banker_cards"])
            winner = data.get("winner") or verdict["winner"]
            self.history = (self.history + winner)[-400:]
            self.rounds.append(dict(data, **verdict))
            self.rounds = self.rounds[-RECENT_LIMIT:]
            warning = verdict.get("warning") or ""
            self.prediction = predict.predict(
                self.shoe, self.history, trials=self.trials,
                markov_weight=self.markov_weight)
        if warning:
            self._status(warning, "warn")
        self._notify()
        self._schedule_analysis()

    def on_state(self, state):
        if self.table and state.get("table") != self.table:
            return
        with self.lock:
            self.state = dict(state)
            self.table = self.table or state.get("table") or ""

    # ---------------------------------------------------------------------- AI
    def _analysis_input(self):
        """Chụp dữ liệu dưới lock rồi đưa sang thread AI."""
        with self.lock:
            return {
                "prediction": self.prediction,
                "recent_rounds": [{
                    "hand": index + 1,
                    "winner": item.get("winner"),
                    "player": item.get("player_cards"),
                    "banker": item.get("banker_cards"),
                } for index, item in enumerate(self.rounds[-8:])],
                "table": self.table,
                "state": (self.state or {}).get("state", ""),
                "time_remains": (self.state or {}).get("time_remains"),
                "history": self.history,
                "round": len(self.rounds),
            }

    def _schedule_analysis(self):
        cfg = ai.config(self._ai_cfg())
        if not ai.available(cfg):
            return
        with self.lock:
            if self.ai_busy or not self.prediction or not self.ai_every:
                return
            if self.ai_every > 1 and len(self.rounds) % self.ai_every != 0:
                return
            self.ai_busy = True
        payload = self._analysis_input()
        threading.Thread(target=self._run_analysis, args=(payload,),
                         daemon=True, name="bcr-live-ai").start()

    def _run_analysis(self, payload):
        try:
            result = ai.analyze_structured(
                payload["prediction"], recent_rounds=payload["recent_rounds"],
                table=payload["table"], state=payload["state"],
                time_remains=payload["time_remains"], history=payload["history"],
                timeout=self.ai_timeout, cfg=self._ai_cfg())
            result = dict(result)
        except Exception as exc:
            result = {"ok": False, "error": "Lỗi AI: %s" % type(exc).__name__}
        with self.lock:
            self.ai_busy = False
            self.ai_error = "" if result.get("ok") else str(result.get("error") or "")
            if result.get("ok"):
                result["round"] = payload["round"]
                result["at"] = time.time()
                self.analysis = result
        self._notify()

    def analyze_now(self):
        """Nút “Phân tích ngay”: gọi đồng bộ, trả kết quả cho RPC."""
        payload = self._analysis_input()
        if not payload["prediction"]:
            return {"ok": False, "error": "Chưa có ván nào để phân tích."}
        cfg = ai.config(self._ai_cfg())
        if not ai.available(cfg):
            return {"ok": False, "error": "Chưa cấu hình AI (chế độ/key/endpoint)."}
        result = ai.analyze_structured(
            payload["prediction"], recent_rounds=payload["recent_rounds"],
            table=payload["table"], state=payload["state"],
            time_remains=payload["time_remains"], history=payload["history"],
            timeout=self.ai_timeout, cfg=self._ai_cfg())
        result = dict(result)
        with self.lock:
            self.ai_error = "" if result.get("ok") else str(result.get("error") or "")
            if result.get("ok"):
                result["round"] = payload["round"]
                result["at"] = time.time()
                self.analysis = result
        self._notify()
        return result

    # ------------------------------------------------------------------ snapshot
    def snapshot(self):
        with self.lock:
            pred = self.prediction
            probabilities = {}
            ev = {}
            uncertainty = {}
            if pred:
                probabilities = {key: _num(value) for key, value in
                                 (pred.get("probabilities") or {}).items()}
                ev = {key: _num(value) for key, value in (pred.get("ev") or {}).items()}
                uncertainty = {key: _num(value) for key, value in
                               (pred.get("ev_uncertainty") or {}).items()}
                shoe = pred.get("shoe") if isinstance(pred.get("shoe"), dict) else {}
                shoe_view = {"remaining": _num(shoe.get("remaining")),
                             "penetration": _num(shoe.get("penetration"))}
            else:
                shoe_view = {"remaining": None, "penetration": None}
            prediction_view = None
            if pred:
                prediction_view = {
                    "ok": bool(pred.get("ok")),
                    "error": pred.get("error"),
                    "probabilities": probabilities,
                    "ev": ev,
                    "ev_uncertainty": uncertainty,
                    "best_side": pred.get("best_side"),
                    "least_bad_side": pred.get("least_bad_side"),
                    "playable": bool(pred.get("playable")),
                    "recommendation": pred.get("recommendation"),
                    "trials": pred.get("trials"),
                    "shoe": shoe_view,
                }
            cfg = ai.config(self._ai_cfg())
            return {
                "ok": bool(pred and pred.get("ok")),
                "running": bool(self.running),
                "table": self.table,
                "state": (self.state or {}).get("state", "") if self.state else "",
                "time_remains": (self.state or {}).get("time_remains") if self.state else None,
                "rounds": len(self.rounds),
                "duplicates": self.duplicates,
                "history_tail": self.history[-60:],
                "prediction": prediction_view,
                "analysis": dict(self.analysis) if isinstance(self.analysis, dict) else None,
                "ai": {
                    "mode": cfg["mode"],
                    "url": cfg["url"] if cfg["mode"] == "local" else "",
                    "model": cfg["model"],
                    "available": ai.available(cfg),
                    "busy": bool(self.ai_busy),
                    "error": self.ai_error,
                },
                "config": {
                    "decks": self.decks,
                    "trials": self.trials,
                    "markov_weight": _num(self.markov_weight),
                    "ai_every": self.ai_every,
                    "ai_timeout": self.ai_timeout,
                },
            }
