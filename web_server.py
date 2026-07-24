#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八癞子掼蛋 Web UI 服务器 — 纯 Flask 壳，所有逻辑在 sort_8laizi 中。
"""
import sys
from flask import Flask, request, jsonify, send_from_directory, make_response

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from sort_8laizi import (
    Card, cards_to_json, cards_to_hex, card_to_hex, sort_8laizi_with_details,
    build_full_deck_cards, validate_deal, build_full_deck, SUITS, RANKS,
    RANK_HEX, SUIT_CODE,
    deal_ba_hong_tao, evaluate_hand_power,
)
from deal_config import default_config, load_config_from_file, load_config_from_ini_text

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    resp = make_response(send_from_directory(".", "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/deck", methods=["GET"])
def api_deck():
    """返回完整 108 张牌库（供配牌弹窗使用）。"""
    level = request.args.get("level", "2")
    wild_mode = int(request.args.get("wild_mode", 8))
    cards = build_full_deck_cards(level, wild_mode=wild_mode)
    return jsonify({
        "cards": cards_to_json(cards),
        "total": len(cards),
        "level": level,
        "wild_mode": wild_mode,
    })


@app.route("/api/deal_custom", methods=["POST"])
def api_deal_custom():
    """
    自定义配牌后发牌：接收 4 个玩家的 cid 列表，校验并返回手牌。
    """
    data = request.get_json() or {}
    level = data.get("level", "2")
    wild_mode = int(data.get("wild_mode", 8))
    player_ids = data.get("players", [[], [], [], []])

    deck = build_full_deck_cards(level, wild_mode=wild_mode)
    cid_to_card = {c.cid: c for c in deck}

    validation = validate_deal(player_ids)
    if not validation["ok"]:
        return jsonify({
            "ok": False,
            "error": validation["error"],
            "counts": validation.get("counts", [len(p) for p in player_ids]),
        }), 400

    # 构建每个玩家的手牌
    players_hands = []
    for ids in player_ids:
        hand = [cid_to_card[cid] for cid in ids]
        players_hands.append(hand)

    # 返回全部4人手牌 + P1信息（兼容旧格式）
    players_json = [cards_to_json(h) for h in players_hands]
    players_hx = [cards_to_hex(h) for h in players_hands]
    wc_list = [sum(1 for c in h if c.is_wild) for h in players_hands]
    print(f"[DEBUG deal_custom] players_json len={len(players_json)}, hex len={len(players_hx)}, wc={wc_list}", flush=True)
    result = {
        "ok": True,
        "hand": cards_to_json(players_hands[0]),
        "hand_hex": cards_to_hex(players_hands[0]),
        "wild_count": sum(1 for c in players_hands[0] if c.is_wild),
        "total": len(players_hands[0]),
        "level": level,
        "wild_mode": wild_mode,
        "all_counts": [len(h) for h in players_hands],
        "players": players_json,
        "players_hex": players_hx,
        "wild_counts": wc_list,
    }
    print(f"[DEBUG deal_custom] result keys: {list(result.keys())}", flush=True)
    return jsonify(result)


@app.route("/api/deal_ba", methods=["POST"])
def api_deal_ba():
    """
    八红桃发牌：按 BaHongTaoDealCard 配置两阶段发牌。

    请求 body (JSON, 均可选):
      seed:     随机种子 (int)
      level:    级牌点数 (str, 默认 "2")
      config:   {
        "wild_tiers": "0-2:69|3-4:20|5-7:10|8:1",  # 可选, 覆盖默认
        "robot_max_wilds": 1,
        "control_mode": 1,
        "compensate_threshold": 30,
        "scores": { "bomb": 30, ... }  # 可选
      }
      config_file: INI 文件路径 (str, 可选, 优先于 config)

    返回:
      players:       [hand_json, ...]  4 人 × 27 张
      players_hex:   [hex_str, ...]
      wild_counts:   [int, ...]
      power_evals:   [{score, details, weights}, ...]
      compensation_log: [{round, from_seat, to_seat, cards_swapped, gap_before}, ...]
      seed, level, total_cards, hand_size
    """
    data = request.get_json() or {}
    seed = data.get("seed")
    level = data.get("level", "2")

    # 构建配置
    config_file = data.get("config_file")
    config_dict = data.get("config")

    if config_file:
        cfg = load_config_from_file(config_file, level=level)
    elif config_dict:
        # 从 dict 构建 DealConfig
        from deal_config import DealConfig, ScoreWeights, WildTier, parse_wild_tiers
        cfg = DealConfig(level=level)
        cfg.wild_mode = int(config_dict.get("wild_mode", 8))
        if "wild_tiers" in config_dict:
            cfg.wild_tiers = parse_wild_tiers(str(config_dict["wild_tiers"]))
        else:
            cfg.wild_tiers = parse_wild_tiers("0-2:69|3-4:20|5-7:10|8:1")
        cfg.robot_max_wilds = int(config_dict.get("robot_max_wilds", 2))
        cfg.control_mode = int(config_dict.get("control_mode", 1))
        cfg.compensate_threshold = float(config_dict.get("compensate_threshold", 30))
        scores_dict = config_dict.get("scores", {})
        cfg.scores = ScoreWeights(
            bomb=int(scores_dict.get("bomb", 30)),
            same_color_link=int(scores_dict.get("same_color_link", 25)),
            steel_plate=int(scores_dict.get("steel_plate", 15)),
            link_pair=int(scores_dict.get("link_pair", 10)),
            big_joker=int(scores_dict.get("big_joker", 10)),
            three=int(scores_dict.get("three", 6)),
            small_joker=int(scores_dict.get("small_joker", 6)),
            wild=int(scores_dict.get("wild", 5)),
        )
    else:
        cfg = default_config(level=level)

    result = deal_ba_hong_tao(cfg, seed=seed, level=level)
    return jsonify(result.to_dict())


@app.route("/api/deal_ba_config", methods=["GET"])
def api_deal_ba_config():
    """返回当前默认发牌配置（供前端展示）。"""
    level = request.args.get("level", "2")
    cfg = default_config(level=level)
    return jsonify({
        "wild_mode": cfg.wild_mode,
        "wild_tiers": [
            {"min": t.min_val, "max": t.max_val, "weight": t.weight}
            for t in cfg.wild_tiers
        ],
        "robot_max_wilds": cfg.robot_max_wilds,
        "control_mode": cfg.control_mode,
        "compensate_threshold": cfg.compensate_threshold,
        "scores": {
            "bomb": cfg.scores.bomb,
            "same_color_link": cfg.scores.same_color_link,
            "steel_plate": cfg.scores.steel_plate,
            "link_pair": cfg.scores.link_pair,
            "big_joker": cfg.scores.big_joker,
            "three": cfg.scores.three,
            "small_joker": cfg.scores.small_joker,
            "wild": cfg.scores.wild,
        },
        "total_cards": cfg.total_cards,
        "total_wilds": cfg.total_wilds,
        "players": cfg.players,
        "hand_size": cfg.hand_size,
        "level": cfg.level,
    })


@app.route("/api/evaluate_power", methods=["POST"])
def api_evaluate_power():
    """评估单副手牌的牌力。"""
    data = request.get_json() or {}
    hand_cards = [Card(
        suit=c["suit"], rank=c["rank"],
        is_wild=c.get("is_wild", False), cid=c.get("cid", 0),
    ) for c in data.get("cards", [])]
    level = data.get("level", "2")
    cfg = default_config(level=level)
    ev = evaluate_hand_power(hand_cards, cfg.scores)
    return jsonify(ev)


@app.route("/api/sort", methods=["POST"])
def api_sort():
    data = request.get_json()
    hand_cards = [Card(
        suit=c["suit"], rank=c["rank"],
        is_wild=c.get("is_wild", False), cid=c.get("cid", 0),
    ) for c in data.get("cards", [])]

    laizi_limit = data.get("laizi_limit")
    wild_mode = data.get("wild_mode", 8)
    # 2癞子模式自动走快速路径，8癞子走完整路径
    fast_mode = (wild_mode == 2)
    result = sort_8laizi_with_details(hand_cards, laizi_limit=laizi_limit, fast_mode=fast_mode)
    result["best_index"] = 0
    # 为每个 zone 的 cards 添加 hex 编码（直接从 dict 计算，避免重复创建 Card）
    for zone_name, groups in result.get("zones", {}).items():
        for g in groups:
            hex_parts = []
            for c in g.get("cards", []):
                suit = c["suit"]
                rank = c["rank"]
                if rank in ("SJ", "BJ"):
                    color = SUIT_CODE[rank]
                else:
                    color = SUIT_CODE.get(suit, 0)
                value = RANK_HEX.get(rank, 0)
                hex_parts.append(f"0x{color:X}{value:X}")
            g["cards_hex"] = ",".join(hex_parts)
    return jsonify(result)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5001))
    print(f"Starting 八癞子掼蛋 Web UI at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
