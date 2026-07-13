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
    Card, deal_random_hand, cards_to_json, cards_to_hex, card_to_hex, sort_8laizi_with_details,
    build_full_deck_cards, validate_deal, build_full_deck, SUITS, RANKS,
    RANK_HEX, SUIT_CODE,
)

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    resp = make_response(send_from_directory(".", "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/deal", methods=["POST"])
def api_deal():
    data = request.get_json() or {}
    level = data.get("level", "2")
    seed = data.get("seed")
    cards = deal_random_hand(level, seed)
    wild_count = sum(1 for c in cards if c.is_wild)
    return jsonify({
        "hand": cards_to_json(cards),
        "hand_hex": cards_to_hex(cards),
        "wild_count": wild_count,
        "total": len(cards),
        "level": level,
        "seed": seed,
    })


@app.route("/api/deck", methods=["GET"])
def api_deck():
    """返回完整 108 张牌库（供配牌弹窗使用）。"""
    level = request.args.get("level", "2")
    cards = build_full_deck_cards(level)
    return jsonify({
        "cards": cards_to_json(cards),
        "total": len(cards),
        "level": level,
    })


@app.route("/api/deal_custom", methods=["POST"])
def api_deal_custom():
    """
    自定义配牌后发牌：接收 4 个玩家的 cid 列表，校验并返回手牌。
    """
    data = request.get_json() or {}
    level = data.get("level", "2")
    player_ids = data.get("players", [[], [], [], []])

    deck = build_full_deck_cards(level)
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

    # 返回 P1 的手牌 + 全部信息
    p1_hand = players_hands[0]
    wild_count = sum(1 for c in p1_hand if c.is_wild)
    return jsonify({
        "ok": True,
        "hand": cards_to_json(p1_hand),
        "hand_hex": cards_to_hex(p1_hand),
        "wild_count": wild_count,
        "total": len(p1_hand),
        "level": level,
        "all_counts": [len(h) for h in players_hands],
    })


@app.route("/api/sort", methods=["POST"])
def api_sort():
    data = request.get_json()
    hand_cards = [Card(
        suit=c["suit"], rank=c["rank"],
        is_wild=c.get("is_wild", False), cid=c.get("cid", 0),
    ) for c in data.get("cards", [])]

    laizi_limit = data.get("laizi_limit")
    result = sort_8laizi_with_details(hand_cards, laizi_limit=laizi_limit)
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
