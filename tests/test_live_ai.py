# -*- coding: utf-8 -*-
"""Kiểm thử luồng AI local/API nối vào UI: prompt, schema, chặn override, RPC.

Không gọi mạng thật: ``urlopen`` được mock. Key trong test là chuỗi giả, không phải
key thật — repo không chứa API key.
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aibcr.controllers.baccarat import BaccaratController
from aibcr.live import ai
from aibcr.live.session import LiveSession

ROUND = {
    "issue_id": "BCR_VV77845-261119-0514",
    "table": "BCR_VV77845",
    "player_cards": [28, 32, 5],
    "banker_cards": [37, 41, 49],
    "winner": "B",
    "bets": [],
}

PREDICTION = {
    "ok": True,
    "probabilities": {"p_player": 0.44, "p_banker": 0.46, "p_tie": 0.10},
    "ev": {"P": -0.01, "B": -0.002, "T": -0.18},
    "ev_uncertainty": {"P": 0.008, "B": 0.008, "T": 0.004},
    "best_side": None,
    "least_bad_side": "B",
    "playable": False,
    "recommendation": "Bỏ tay: EV âm.",
    "trials": 600,
    "shoe": {"remaining": 410, "penetration": 0.015},
}


class FakeResponse:
    def __init__(self, content):
        self.content = content if isinstance(content, str) else json.dumps(content)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.content.encode("utf-8", "replace")


def api_reply(content):
    return {"choices": [{"message": {"content": content}}]}


class AiConfigTests(unittest.TestCase):
    def setUp(self):
        cleaner = patch.dict(os.environ, {
            "BCR_AI_KEY": "", "BCR_AI_MODE": "", "BCR_AI_URL": "",
            "BCR_AI_MODEL": "", "BCR_AI_TIMEOUT": "",
        })
        cleaner.start()
        self.addCleanup(cleaner.stop)

    def test_modes(self):
        self.assertFalse(ai.available(ai.config({"mode": "off"})))
        self.assertTrue(ai.available(ai.config({
            "mode": "local", "url": "http://127.0.0.1:11434/v1/chat/completions"})))
        self.assertFalse(ai.available(ai.config({"mode": "api", "key": ""})))
        self.assertTrue(ai.available(ai.config({"mode": "api", "key": "dummy"})))

    def test_missing_key_reports_error(self):
        result = ai.analyze(PREDICTION, cfg={"mode": "api"})
        self.assertFalse(result["ok"])
        self.assertIn("BCR_AI_KEY", result["error"])

    def test_build_prompt_keeps_numbers_and_history(self):
        prompt = ai.build_prompt(PREDICTION, history="PBBPT", json_mode=True)
        self.assertIn('"probabilities"', prompt)
        self.assertIn('"playable": false', prompt)
        self.assertIn('"history_recent": "PBBPT"', prompt)

    def test_structured_forces_bo_tay_and_drops_ai_numbers(self):
        fake = {
            "verdict": "Đánh", "focus_side": "P", "confidence": "cao",
            "risk": "rủi ro giả", "explanation": "giải thích giả",
            "probabilities": {"p_player": 0.99},
        }
        with patch.object(ai, "urlopen", return_value=FakeResponse(api_reply(json.dumps(fake)))):
            result = ai.analyze_structured(PREDICTION, cfg={"mode": "api", "key": "dummy"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "BỎ TAY")
        self.assertEqual(result["confidence"], "thấp")
        self.assertEqual(result["focus_side"], "B")
        self.assertNotIn("probabilities", result)
        self.assertEqual(result["risk"], "rủi ro giả")

    def test_local_endpoint_needs_no_key(self):
        fake = {"verdict": "Cân nhắc", "focus_side": "B", "confidence": "trung bình",
                "risk": "", "explanation": "ổn"}
        cfg = {"mode": "local", "url": "http://localhost:11434/v1/chat/completions"}
        with patch.object(ai, "urlopen", return_value=FakeResponse(api_reply(json.dumps(fake)))):
            result = ai.analyze_structured(PREDICTION, cfg=cfg)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "BỎ TAY")  # playable=false vẫn là chốt

    def test_text_fallback_when_model_returns_plain_text(self):
        with patch.object(ai, "urlopen",
                          return_value=FakeResponse(api_reply("AI trả text thường"))):
            result = ai.analyze_structured(PREDICTION, cfg={"mode": "api", "key": "dummy"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "text")
        self.assertEqual(result["verdict"], "BỎ TAY")
        self.assertIn("AI trả text thường", result["explanation"])

    def test_retry_without_response_format_on_http_400(self):
        error = HTTPError("http://api", 400, "bad request", {}, None)
        second = api_reply(json.dumps({
            "verdict": "BỎ TAY", "risk": "", "explanation": "ok"}))
        with patch.object(ai, "urlopen", side_effect=[
                error, FakeResponse(second)]) as mocked:
            result = ai.analyze_structured(PREDICTION, cfg={"mode": "api", "key": "dummy"})
        self.assertTrue(result["ok"])
        self.assertEqual(mocked.call_count, 2)
        body = json.loads(mocked.call_args_list[0].args[0].data.decode("utf-8"))
        self.assertIn("response_format", body)
        body2 = json.loads(mocked.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertNotIn("response_format", body2)


class SessionTests(unittest.TestCase):
    def setUp(self):
        cleaner = patch.dict(os.environ, {"BCR_AI_KEY": "", "BCR_AI_MODE": ""})
        cleaner.start()
        self.addCleanup(cleaner.stop)

    def test_prediction_and_snapshot(self):
        session = LiveSession(trials=600, ai_mode="off")
        session.on_round(dict(ROUND))
        snapshot = session.snapshot()
        self.assertTrue(snapshot["ok"])
        self.assertAlmostEqual(sum(snapshot["prediction"]["probabilities"].values()),
                               1.0, places=5)
        self.assertEqual(snapshot["rounds"], 1)
        self.assertIsNone(snapshot["analysis"])

    def test_duplicate_issue_does_not_touch_shoe(self):
        session = LiveSession(trials=600, ai_mode="off")
        session.on_round(dict(ROUND))
        dealt = session.shoe.dealt
        session.on_round(dict(ROUND))
        self.assertEqual(session.shoe.dealt, dealt)
        self.assertEqual(session.duplicates, 1)

    def test_analyze_now_without_config(self):
        session = LiveSession(trials=600, ai_mode="api")
        session.on_round(dict(ROUND))
        result = session.analyze_now()
        self.assertFalse(result["ok"])


class RpcTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_live_methods_are_wired(self):
        controller = BaccaratController(
            str(pathlib.Path(self.tmp.name) / "state.json"),
            type("Emitter", (), {"event": lambda self, *a, **kw: None})())
        self.addCleanup(controller.shutdown)
        result, error = controller.dispatch("live.state", {})
        self.assertIsNone(error)
        self.assertFalse(result["running"])
        result, error = controller.dispatch("live.analyze", {})
        self.assertIsNone(error)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
