#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八癞子掼蛋 Web UI 服务器 — 纯 Flask 壳，所有逻辑在 sort_8laizi 中。
"""
import sys
from flask import Flask, request, jsonify, send_from_directory

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from sort_8laizi import (
    Card, deal_random_hand, cards_to_json, sort_8laizi_with_details
)

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/deal", methods=["POST"])
def api_deal():
    data = request.get_json() or {}
    level = data.get("level", "2")
    seed = data.get("seed")
    cards = deal_random_hand(level, seed)
    wild_count = sum(1 for c in cards if c.is_wild)
    return jsonify({
        "hand": cards_to_json(cards),
        "wild_count": wild_count,
        "total": len(cards),
        "level": level,
        "seed": seed,
    })


@app.route("/api/sort", methods=["POST"])
def api_sort():
    data = request.get_json()
    hand_cards = [Card(
        suit=c["suit"], rank=c["rank"],
        is_wild=c.get("is_wild", False), cid=c.get("cid", 0),
    ) for c in data.get("cards", [])]

    result = sort_8laizi_with_details(hand_cards)
    if len(result["all_results"]) > 50:
        result["all_results"] = result["all_results"][:50]
    result["best_index"] = 0
    return jsonify(result)


if __name__ == "__main__":
    print("Starting 八癞子掼蛋 Web UI at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
