# -*- coding: utf-8 -*-
"""Xác suất tay kế tiếp cho BCR từ bộ bài còn lại.

Ba lớp, mỗi lớp nói rõ nó đo cái gì:

    Monte Carlo   mô phỏng đúng luật chia bài Baccarat trên composition của shoe
                  còn lại -> p(P), p(B), p(T). Đây là phần duy nhất có cơ sở toán.
    Markov        ma trận chuyển tiếp B/P/T từ lịch sử bàn, làm mượt Laplace.
                  Thực nghiệm gần như đồng nhất, chỉ dùng làm tín hiệu phụ.
    Kết hợp       trộn MC với Markov theo ``markov_weight`` (mặc định 0.15).

``predict`` trả cả EV từng cửa để biết có nên cược hay đứng ngoài.
"""

import random

from aibcr.live import cards

SIDES = ("P", "B", "T")
PAYOUT = {"B": 0.95, "P": 1.0, "T": 8.0}
DEFAULT_TRIALS = 20000
DEFAULT_MARKOV_WEIGHT = 0.15


def _draw(counts, rng):
    """Rút một lá theo trọng số còn lại (index 0..9 = điểm 0..9). Trả điểm."""
    total = sum(counts)
    if total <= 0:
        return None
    pick = rng.random() * total
    acc = 0.0
    for value, count in enumerate(counts):
        if count <= 0:
            continue
        acc += count
        if pick < acc:
            return value
    for value in range(9, -1, -1):
        if counts[value] > 0:
            return value
    return None


def _take(counts, value):
    if counts[value] <= 0:
        return False
    counts[value] -= 1
    return True


def _banker_draws(banker_total, player_third):
    if banker_total <= 2:
        return True
    if banker_total == 3:
        return player_third != 8
    if banker_total == 4:
        return 2 <= player_third <= 7
    if banker_total == 5:
        return 4 <= player_third <= 7
    if banker_total == 6:
        return player_third in (6, 7)
    return False


def play_round(counts, rng):
    """Một ván theo luật Baccarat chuẩn (rút không hoàn lại). Trả 'P'/'B'/'T'."""
    player = [_draw(counts, rng), _draw(counts, rng)]
    banker = [_draw(counts, rng), _draw(counts, rng)]
    if None in player or None in banker:
        return None
    for value in player + banker:
        _take(counts, value)
    p_total = sum(player) % 10
    b_total = sum(banker) % 10
    if p_total in (8, 9) or b_total in (8, 9):
        return _winner(p_total, b_total)
    player_third = None
    if p_total <= 5:
        player_third = _draw(counts, rng)
        if player_third is None:
            return None
        _take(counts, player_third)
        p_total = (p_total + player_third) % 10
    if player_third is None:
        if b_total <= 5:
            third = _draw(counts, rng)
            if third is None:
                return None
            _take(counts, third)
            b_total = (b_total + third) % 10
    elif _banker_draws(b_total, player_third):
        third = _draw(counts, rng)
        if third is None:
            return None
        _take(counts, third)
        b_total = (b_total + third) % 10
    return _winner(p_total, b_total)


def _winner(p_total, b_total):
    if p_total == b_total:
        return "T"
    return "P" if p_total > b_total else "B"


def monte_carlo(counts, trials=DEFAULT_TRIALS, seed=None):
    """Chạy ``trials`` ván trên composition hiện tại; trả dict xác suất + thống kê."""
    trials = max(100, int(trials))
    rng = random.Random(seed)
    tally = {"P": 0, "B": 0, "T": 0}
    done = 0
    for _ in range(trials):
        outcome = play_round(list(counts), rng)
        if outcome is None:
            break
        tally[outcome] += 1
        done += 1
    if not done:
        return {"trials": 0, "p_player": None, "p_banker": None, "p_tie": None}
    return {
        "trials": done,
        "p_player": tally["P"] / done,
        "p_banker": tally["B"] / done,
        "p_tie": tally["T"] / done,
    }


def markov(history, smoothing=1.0, min_transitions=12):
    """Ma trận chuyển B/P/T + xác suất tay kế tiếp theo trạng thái cuối.

    Chưa đủ ``min_transitions`` cặp tay thì trả ``None`` cho xác suất: làm mượt
    Laplace trên dữ liệu rỗng chỉ tạo ra phân phối đều 1/3 và sẽ kéo lệch kết quả
    Monte Carlo một cách vô căn cứ.
    """
    letters = [ch for ch in str(history or "") if ch in SIDES]
    index = {side: i for i, side in enumerate(SIDES)}
    table = [[smoothing] * 3 for _ in range(3)]
    transitions = 0
    for previous, current in zip(letters, letters[1:]):
        table[index[previous]][index[current]] += 1
        transitions += 1
    counts = [int(value - smoothing) for value in table[index[letters[-1]]]] if letters \
        else [0, 0, 0]
    if not letters or transitions < min_transitions:
        return {"state": letters[-1] if letters else None,
                "p_player": None, "p_banker": None, "p_tie": None,
                "counts": counts, "transitions": transitions}
    row = table[index[letters[-1]]]
    total = sum(row)
    return {
        "state": letters[-1],
        "p_player": row[index["P"]] / total,
        "p_banker": row[index["B"]] / total,
        "p_tie": row[index["T"]] / total,
        "counts": counts,
        "transitions": transitions,
    }


def expected_value(probabilities):
    """EV của mỗi cửa với 1 đơn vị cược (đã tính hoa hồng Banker)."""
    p_player = probabilities.get("p_player") or 0.0
    p_banker = probabilities.get("p_banker") or 0.0
    p_tie = probabilities.get("p_tie") or 0.0
    return {
        "P": p_player * PAYOUT["P"] - (1 - p_player),
        "B": p_banker * PAYOUT["B"] - (1 - p_banker),
        "T": p_tie * PAYOUT["T"] - (1 - p_tie),
    }


def ev_standard_error(probability, trials, payout):
    """Sai số chuẩn của EV do Monte Carlo hữu hạn (không phải sai số của trò chơi)."""
    trials = max(1, int(trials))
    probability = max(0.0, min(1.0, float(probability)))
    return ((probability * (1 - probability) / trials) ** 0.5) * (1 + payout)


def _ev_uncertainty(probabilities, trials):
    return {
        "P": ev_standard_error(probabilities["p_player"], trials, PAYOUT["P"]),
        "B": ev_standard_error(probabilities["p_banker"], trials, PAYOUT["B"]),
        "T": ev_standard_error(probabilities["p_tie"], trials, PAYOUT["T"]),
    }


def advance_round(shoe, player_cards, banker_cards):
    """Trừ các lá đã chia khỏi shoe; trả dict kết quả + cảnh báo nếu shoe cạn."""
    warning = ""
    for card in list(player_cards) + list(banker_cards):
        try:
            shoe.take(card)
        except ValueError as exc:
            warning = str(exc)
            break
    player_total = cards.hand_total(player_cards)
    banker_total = cards.hand_total(banker_cards)
    return {
        "player_total": player_total,
        "banker_total": banker_total,
        "player_natural": cards.is_natural(player_cards),
        "banker_natural": cards.is_natural(banker_cards),
        "winner": _winner(player_total, banker_total),
        "warning": warning,
    }


def _blend(mc, mk, weight):
    """Trộn MC với Markov; Markov thiếu dữ liệu (p_* = None) thì dùng MC thuần."""
    weight = max(0.0, min(1.0, float(weight)))
    if not mc or mc.get("p_player") is None:
        return {"p_player": mk["p_player"], "p_banker": mk["p_banker"], "p_tie": mk["p_tie"]}
    if not mk or mk.get("p_player") is None:
        return {"p_player": mc["p_player"], "p_banker": mc["p_banker"], "p_tie": mc["p_tie"]}
    return {
        "p_player": (1 - weight) * mc["p_player"] + weight * mk["p_player"],
        "p_banker": (1 - weight) * mc["p_banker"] + weight * mk["p_banker"],
        "p_tie": (1 - weight) * mc["p_tie"] + weight * mk["p_tie"],
    }


def predict(shoe, history="", trials=DEFAULT_TRIALS, markov_weight=DEFAULT_MARKOV_WEIGHT,
            seed=None):
    """Dự đoán tay kế tiếp: xác suất, EV, cửa nên chọn, và lý do ngắn."""
    counts = shoe.value_counts() if hasattr(shoe, "value_counts") else list(shoe)
    if sum(counts) <= 0:
        return {"ok": False, "error": "Shoe đã hết bài; cần xào lại trước khi dự đoán."}
    mc = monte_carlo(counts, trials=trials, seed=seed)
    if mc.get("trials", 0) == 0:
        return {"ok": False, "error": "Không mô phỏng được ván nào từ shoe hiện tại."}
    mk = markov(history)
    fused = _blend(mc, mk, markov_weight)
    ev = expected_value(fused)
    uncertainty = _ev_uncertainty(fused, mc["trials"])
    ranked = max(SIDES, key=lambda side: ev[side])
    # EV dương nhỏ hơn ~2 sai số chuẩn của Monte Carlo chỉ là nhiễu mô phỏng, không phải
    # lợi thế thật: 20k ván cho SE ≈ 0,008 nên EV +0,004 không có nghĩa gì.
    playable = ev[ranked] > 2.0 * max(uncertainty.values())
    return {
        "ok": True,
        "trials": mc["trials"],
        "monte_carlo": {"p_player": mc["p_player"], "p_banker": mc["p_banker"],
                        "p_tie": mc["p_tie"]},
        "markov": mk,
        "probabilities": fused,
        "ev": ev,
        "ev_uncertainty": uncertainty,
        "best_side": ranked if playable else None,
        "least_bad_side": ranked,
        "best_ev": ev[ranked],
        "edge": ev[ranked] if playable else 0.0,
        "playable": playable,
        "recommendation": (
            "Đánh %s (EV %+.4f ± %.4f mỗi 1 đơn vị)." % (ranked, ev[ranked],
                                                         uncertainty[ranked])
            if playable else
            "Bỏ tay: EV tốt nhất (%s %+.4f) vẫn âm hoặc nằm trong nhiễu mô phỏng ±%.4f."
            % (ranked, ev[ranked], uncertainty[ranked])),
        "shoe": shoe.snapshot() if hasattr(shoe, "snapshot") else {"value_counts": counts},
    }
