#!/usr/bin/env python3
"""
八红桃掼蛋 — 完整发牌模拟 & 牌力评估
=========================================
108 张牌（2 副标准扑克），8 张级牌 = 万能牌（癞子）
按发牌控制策略分配癞子后，评估每人的手牌强度
"""

import random
import json
import sys
from collections import Counter, defaultdict
from typing import Literal

# ─── 牌库构建 ─────────────────────────────────────────
SUITS = ["♠", "♥", "♣", "♦"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
JOKERS = ["🃏小", "🃏大"]  # 小王、大王

def build_deck(level: str = "2") -> list[dict]:
    """构建 2 副牌 = 108 张，标记哪些是级牌（将是癞子）"""
    deck = []
    for _ in range(2):  # 两副
        for suit in SUITS:
            for rank in RANKS:
                card = {"suit": suit, "rank": rank, "lz": False}
                if rank == level:
                    card["lz"] = True  # 级牌 = 癞子
                deck.append(card)
    # Jokers: 2×小王 + 2×大王
    for _ in range(2):
        deck.append({"suit": "JOKER", "rank": "小", "lz": False})
    for _ in range(2):
        deck.append({"suit": "JOKER", "rank": "大", "lz": False})
    return deck


# ─── 手牌评估器 ────────────────────────────────────────

def evaluate_hand(cards: list[dict]) -> dict:
    """
    评估一手牌的强度，返回多维度指标。

    核心思路：手牌 = 自然牌 + 癞子
    癞子可以充当「最佳缺牌」——评估各种牌型的完成度
    """
    laizi = [c for c in cards if c["lz"]]
    natural = [c for c in cards if not c["lz"]]
    nlz = len(laizi)

    # ── 自然牌统计 ──
    rank_counts = Counter(c["rank"] for c in natural)
    suit_counts = Counter(c["suit"] for c in natural)
    jokers_big = sum(1 for c in natural if c["rank"] == "大")
    jokers_small = sum(1 for c in natural if c["rank"] == "小")

    # ── 炸弹潜力（含癞子） ──
    nat_bombs = sum(1 for v in rank_counts.values() if v >= 4)  # 自然炸弹
    potential_bombs = 0
    for rank, cnt in rank_counts.items():
        if cnt + nlz >= 4 and cnt < 4:
            potential_bombs += 1
    # 还有「两处凑一个炸弹」的情况：两个不同 rank 的 3 张 + 2 癞子
    triple_ranks = [r for r, c in rank_counts.items() if c == 3]
    if len(triple_ranks) >= 2 and nlz >= 2:
        potential_bombs += 1

    # ── 同花顺潜力 ──
    # 找到最长同花色 + 癞子能补成顺子的数量
    flush_potential = 0
    for suit in SUITS:
        suit_ranks = [c["rank"] for c in natural if c["suit"] == suit]
        rank_indices = sorted(set(RANKS.index(r) for r in suit_ranks if r in RANKS))
        # 贪心找最长连续段，断点用癞子补
        used_lz = 0
        max_len = 0
        cur_len = 1
        for i in range(1, len(rank_indices)):
            gap = rank_indices[i] - rank_indices[i - 1] - 1
            if gap == 0:
                cur_len += 1
            elif gap <= (nlz - used_lz):
                used_lz += gap
                cur_len += gap + 1
            else:
                max_len = max(max_len, cur_len + (nlz - used_lz))
                cur_len = 1
                used_lz = 0
        max_len = max(max_len, cur_len + (nlz - used_lz))
        if max_len >= 5:
            flush_potential += 1

    # ── 对子/三张/连对 ──
    pairs_nat = sum(1 for v in rank_counts.values() if v >= 2)
    triples_nat = sum(1 for v in rank_counts.values() if v >= 3)
    # 癞子可以成对：1 癞子 + 任意单牌 = 对子
    singles = sum(1 for v in rank_counts.values() if v == 1)
    extra_pairs = min(nlz, singles)  # 每个癞子+单牌 = 对子
    # 癞子可以成三张：2 癞子 + 任意单牌 = 三张
    extra_triples = min(nlz // 2, singles)

    # ── 综合评分（粗粒度） ──
    score = (
        (nat_bombs + potential_bombs) * 30   # 炸弹权重最高
        + flush_potential * 20                # 同花顺
        + (pairs_nat + extra_pairs) * 2       # 对子
        + (triples_nat + extra_triples) * 5   # 三张
        + jokers_big * 8                      # 大王
        + jokers_small * 4                    # 小王
        + nlz * 5                             # 癞子本身灵活分
    )

    return {
        "nlz": nlz,
        "nat_bombs": nat_bombs,
        "potential_bombs": potential_bombs,
        "flush_potential": flush_potential,
        "pairs_nat": pairs_nat,
        "extra_pairs": extra_pairs,
        "triples_nat": triples_nat,
        "extra_triples": extra_triples,
        "jokers_big": jokers_big,
        "jokers_small": jokers_small,
        "score": score,
    }


# ─── 发牌控制 ─────────────────────────────────────────

TIERS = [(1, 2, 69), (3, 4, 20), (5, 7, 10), (8, 8, 1)]
TOTAL_LAIZI = 8
PLAYERS = 4

def roll_tier():
    weights = [t[2] for t in TIERS]
    tier = random.choices(TIERS, weights=weights, k=1)[0]
    return tier[0], tier[1]


def alloc_laizi_independent() -> list[int]:
    """方案 B：4 人独立摇号 → 削峰填谷 → 随机分配座位"""
    raw = []
    for _ in range(PLAYERS):
        lo, hi = roll_tier()
        raw.append(random.randint(lo, hi))

    total = sum(raw)
    # 削峰：从多的人逐张减
    while total > TOTAL_LAIZI:
        idx = raw.index(max(raw))
        raw[idx] -= 1
        total -= 1
    # 填谷：给少的人逐张加
    while total < TOTAL_LAIZI:
        idx = raw.index(min(raw))
        raw[idx] += 1
        total += 1

    # 随机打乱座位
    random.shuffle(raw)
    return raw


# ─── 主模拟 ─────────────────────────────────────────

def simulate_full(rounds: int = 10000, level: str = "2"):
    deck = build_deck(level)
    # 分离癞子牌和非癞子牌
    laizi_cards = [c for c in deck if c["lz"]]
    assert len(laizi_cards) == 8, f"Expected 8 laizi, got {len(laizi_cards)}"

    natural_pool = [c for c in deck if not c["lz"]]

    # 累积统计
    score_by_nlz = defaultdict(list)  # nlz → [scores...]
    player_scores = [[] for _ in range(PLAYERS)]
    player_nlz = [[] for _ in range(PLAYERS)]

    for _ in range(rounds):
        counts = alloc_laizi_independent()

        # 把癞子牌和自然牌洗好
        random.shuffle(laizi_cards)
        random.shuffle(natural_pool)

        # 分配牌：先按癞子数量发癞子，再均匀发自然牌（25-癞子数）
        nlz_cards = laizi_cards.copy()
        hands = [[] for _ in range(PLAYERS)]
        for i, n in enumerate(counts):
            hands[i].extend(nlz_cards[:n])
            nlz_cards = nlz_cards[n:]

        # 发自然牌：每人 27 - 癞子数 张
        ptr = 0
        for i in range(PLAYERS):
            need = 27 - counts[i]
            hands[i].extend(natural_pool[ptr:ptr + need])
            ptr += need

        # 评估
        for i in range(PLAYERS):
            ev = evaluate_hand(hands[i])
            score_by_nlz[counts[i]].append(ev["score"])
            player_scores[i].append(ev["score"])
            player_nlz[i].append(counts[i])

    return score_by_nlz, player_scores, player_nlz


# ─── 报告 ────────────────────────────────────────────

def print_report(rounds: int = 50000):
    print(f"{'='*65}")
    print(f"  八红桃掼蛋 — 完整牌力评估（方案 B：独立摇号+削峰填谷+随机座次）")
    print(f"  模拟次数：{rounds:,}  108 张牌 / 8 癞子级牌 / 4 人各 27 张")
    print(f"{'='*65}")

    score_by_nlz, player_scores, player_nlz = simulate_full(rounds)

    # ── 癞子数量 → 牌力评分 ──
    print(f"\n  {'─'*55}")
    print(f"  癞子数量 vs 手牌强度")
    print(f"  {'─'*55}")
    print(f"  {'癞子数':>6}  {'出现次数':>8}  {'占比':>7}  {'均分':>7}  {'最低分':>7}  {'最高分':>7}")
    for nlz in sorted(score_by_nlz.keys()):
        scores = score_by_nlz[nlz]
        avg = sum(scores) / len(scores)
        pct = len(scores) / (rounds * PLAYERS) * 100
        print(f"  {nlz:>6}  {len(scores):>8}  {pct:>6.2f}%  {avg:>7.0f}  {min(scores):>7}  {max(scores):>7}")

    # ── 每位玩家长期分数 ──
    print(f"\n  {'─'*55}")
    print(f"  各玩家长期牌力（方案 B 随机座次 → 应均衡）")
    print(f"  {'─'*55}")
    print(f"  {'玩家':>6}  {'均分':>7}  {'均癞子':>7}  {'最低分':>7}  {'最高分':>7}  {'≥5癞子%':>8}")
    for i in range(PLAYERS):
        scores = player_scores[i]
        nlz_list = player_nlz[i]
        avg = sum(scores) / len(scores)
        avg_nlz = sum(nlz_list) / len(nlz_list)
        high_nlz = sum(1 for n in nlz_list if n >= 5) / len(nlz_list) * 100
        print(f"  {i+1:>6}  {avg:>7.0f}  {avg_nlz:>7.2f}  {min(scores):>7}  {max(scores):>7}  {high_nlz:>7.1f}%")

    # ── 癞子分布 ──
    nlz_counter = Counter()
    for counts in player_nlz:
        for c in counts:
            nlz_counter[c] += 1
    print(f"\n  {'─'*55}")
    print(f"  癞子数量总体分布")
    print(f"  {'─'*55}")
    for nlz in sorted(nlz_counter.keys()):
        pct = nlz_counter[nlz] / (rounds * PLAYERS) * 100
        bar = "█" * int(pct * 2)
        print(f"  {nlz} 张  {pct:>5.1f}%  {bar}")

    # ── 关键发现 ──
    print(f"\n  {'─'*55}")
    print(f"  📊 关键发现")
    print(f"  {'─'*55}")

    # 癞子 2 张 vs 6 张的分数比
    if 2 in score_by_nlz and 6 in score_by_nlz:
        s2 = sum(score_by_nlz[2]) / len(score_by_nlz[2])
        s6 = sum(score_by_nlz[6]) / len(score_by_nlz[6])
        print(f"  2 癞子均分：{s2:.0f}  |  6 癞子均分：{s6:.0f}  |  差距：{s6-s2:.0f} ({(s6/s2-1)*100:.0f}%)")

    # 炸弹维度对比
    if 0 in score_by_nlz and 4 in score_by_nlz:
        # 取最后几轮的 eval 数据重新跑一次细分统计
        pass

    print(f"\n  方案 B 特点：随机座次 + 削峰填谷，癞子期望 2.0/人，牌力均衡 ✅")
    print()


if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
    print_report(rounds)
