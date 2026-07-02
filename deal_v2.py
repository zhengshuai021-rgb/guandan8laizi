#!/usr/bin/env python3
"""
八红桃掼蛋 — 完整发牌控制模拟器 v2
=====================================
两阶段发牌：
  阶段一：按概率配置分配 8 张癞子（方案 B：独立摇号+削峰填谷+随机座次）
  阶段二：发剩余 100 张牌，计算牌力后补偿调整

癞子复用规则：同一张癞子可以同时计入炸弹、同花顺、对子、三张等
              （代表"灵活性价值"，而非实际同时出牌能力）
"""

import random
import sys
from collections import Counter, defaultdict
from typing import Literal

# ─── 牌库 ─────────────────────────────────────────────
SUITS = ["♠", "♥", "♣", "♦"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
RANK_IDS = {r: i for i, r in enumerate(RANKS)}  # for straight check
ALL_RANKS = RANKS + ["小", "大"]
TOTAL_CARDS = 108  # 2副牌
PLAYERS = 4
HAND_SIZE = 27
TOTAL_LAIZI = 8
NON_LAIZI = TOTAL_CARDS - TOTAL_LAIZI  # 100

# 癞子概率档位
TIERS = [(1, 2, 69), (3, 4, 20), (5, 7, 10), (8, 8, 1)]


def build_deck(level: str = "2") -> list[tuple[str, str, bool]]:
    """构建 2 副牌，返回 [(suit, rank, is_laizi), ...]"""
    deck = []
    for _ in range(2):
        for suit in SUITS:
            for rank in RANKS:
                deck.append((suit, rank, rank == level))
    for _ in range(2):
        deck.append(("JOKER", "小", False))
    for _ in range(2):
        deck.append(("JOKER", "大", False))
    return deck


# ═══════════════════════════════════════════════════════
#  手牌评估器（癞子复用模式）
# ═══════════════════════════════════════════════════════

def evaluate_hand_v2(natural: list[tuple[str, str]], nlz: int) -> dict:
    """
    评估一手牌的全部牌型潜力（癞子复用：每项独立评估，癞子池不互斥）

    natural: [(suit, rank), ...]  不含癞子的自然牌
    nlz: 癞子数量
    """
    rank_cnt = Counter(r for _, r in natural)
    suit_ranks = defaultdict(list)
    for s, r in natural:
        if r in RANK_IDS:
            suit_ranks[s].append(r)

    big_joker = sum(1 for _, r in natural if r == "大")
    small_joker = sum(1 for _, r in natural if r == "小")

    # ── ① 炸弹（复用计数） ──
    # 每个 rank，如果 cnt + nlz >= 4，就算一个炸弹
    bombs = 0
    for r in RANKS:
        if rank_cnt.get(r, 0) + nlz >= 4:
            bombs += 1
    # 癞子自身也能成炸弹：如果 nlz >= 4，多余的癞子自己组炸弹
    if nlz >= 4:
        bombs += 1  # 4+ 癞子本身就是一个活炸弹

    # ── ② 同花顺（复用计数） ──
    flushes = 0
    for suit in SUITS:
        ranks = sorted(set(RANK_IDS[r] for r in suit_ranks[suit] if r in RANK_IDS))
        if not ranks:
            continue
        # 滑动窗口：区间内断点用癞子补
        for start in range(len(ranks)):
            used = 0
            for end in range(start, len(ranks)):
                if end > start:
                    gap = ranks[end] - ranks[end - 1] - 1
                    used += gap
                if used > nlz:
                    break
                length = (ranks[end] - ranks[start] + 1) + max(0, nlz - used)
                # 实际长度受限于牌边界 [0, 12]
                max_extend = min(12 - ranks[end], nlz - used)
                length = min(length + max_extend, 13)  # 最多 13 (A-K)
                if length >= 5:
                    flushes += 1
                    break  # 找到一个就够
            if any(ranks[end] - ranks[start] + 1 + max(0, nlz) >= 5 for end in range(start, len(ranks))):
                pass  # already counted above

    # ── ③ 对子（复用计数） ──
    pairs = 0
    for r in RANKS:
        if rank_cnt.get(r, 0) + nlz >= 2:
            pairs += 1
    # 纯癞子对：nlz >= 2 时额外算
    if nlz >= 2:
        pairs += nlz // 2

    # ── ④ 三张（复用计数） ──
    triples = 0
    for r in RANKS:
        if rank_cnt.get(r, 0) + nlz >= 3:
            triples += 1
    if nlz >= 3:
        triples += nlz // 3

    # ── ⑤ 钢板（复用计数） ──
    # 两个相邻 rank 都有 ≥3（含癞子）
    steels = 0
    for i in range(len(RANKS) - 1):
        r1, r2 = RANKS[i], RANKS[i + 1]
        if rank_cnt.get(r1, 0) + nlz >= 3 and rank_cnt.get(r2, 0) + nlz >= 3:
            steels += 1

    # ── ⑥ 连对（复用计数） ──
    # 3+ 连续 rank 各有 ≥2
    runs = 0
    cur = 0
    for r in RANKS:
        if rank_cnt.get(r, 0) + nlz >= 2:
            cur += 1
            if cur >= 3:
                runs += 1
        else:
            cur = 0

    # ── 综合评分 ──
    score = (
        bombs * 30
        + flushes * 25
        + steels * 15
        + triples * 6
        + pairs * 2
        + runs * 10
        + big_joker * 10
        + small_joker * 6
        + nlz * 5
    )

    return {
        "nlz": nlz,
        "bombs": bombs,
        "flushes": flushes,
        "pairs": pairs,
        "triples": triples,
        "steels": steels,
        "runs": runs,
        "big_joker": big_joker,
        "small_joker": small_joker,
        "score": score,
    }


# ═══════════════════════════════════════════════════════
#  发牌控制策略
# ═══════════════════════════════════════════════════════

def alloc_laizi() -> list[int]:
    """阶段一：独立摇号+削峰填谷+随机座次 分配癞子"""
    raw = []
    for _ in range(PLAYERS):
        lo, hi = random.choices(TIERS, weights=[t[2] for t in TIERS], k=1)[0][:2]
        raw.append(random.randint(lo, hi))
    total = sum(raw)
    while total > TOTAL_LAIZI:
        raw[raw.index(max(raw))] -= 1
        total -= 1
    while total < TOTAL_LAIZI:
        raw[raw.index(min(raw))] += 1
        total += 1
    random.shuffle(raw)
    return raw


def deal_controlled(natural_pool: list, laizi_counts: list[int],
                    control: Literal["none", "batch", "compensate"] = "compensate",
                    batch_size: int = 8) -> list[list]:
    """
    阶段二：发自然牌，可选控制策略

    control modes:
      "none"       — 完全随机发（基准线）
      "batch"      — 分批发放，每批后评估，弱的人下批优先
      "compensate" — 先随机发完，然后评估，强弱对调几张牌
    """
    pool = list(natural_pool)
    random.shuffle(pool)
    hands = [[] for _ in range(PLAYERS)]

    if control == "none":
        ptr = 0
        for i in range(PLAYERS):
            need = HAND_SIZE - laizi_counts[i]
            hands[i].extend(pool[ptr:ptr + need])
            ptr += need

    elif control == "batch":
        # 分批发，每批后评估，下一批优先给弱者
        remaining = [HAND_SIZE - lz for lz in laizi_counts]
        ptr = 0
        while ptr < len(pool):
            # 取出本批牌
            batch = pool[ptr:ptr + batch_size]
            ptr += batch_size
            if not batch:
                break

            # 按当前缺牌数排序：缺得多先发
            order = sorted(range(PLAYERS), key=lambda i: remaining[i], reverse=True)
            cards_per = max(1, len(batch) // PLAYERS)

            for idx, pi in enumerate(order):
                if remaining[pi] <= 0:
                    continue
                start = idx * cards_per
                end = min(start + cards_per, len(batch))
                give = min(len(batch[start:end]), remaining[pi])
                hands[pi].extend(batch[start:start + give])
                remaining[pi] -= give

        # 如果有剩余没发完的（极少情况），补充给缺牌的人
        leftover = pool[ptr:]
        for c in leftover:
            neediest = min(range(PLAYERS), key=lambda i: remaining[i])
            if remaining[neediest] > 0:
                hands[neediest].append(c)
                remaining[neediest] -= 1

    elif control == "compensate":
        # 先随机发完
        ptr = 0
        for i in range(PLAYERS):
            need = HAND_SIZE - laizi_counts[i]
            hands[i].extend(pool[ptr:ptr + need])
            ptr += need

        # 评估后，从最强的拿若干张补给最弱的
        evals = [evaluate_hand_v2(hands[i], laizi_counts[i]) for i in range(PLAYERS)]
        scores = [e["score"] for e in evals]
        strongest = scores.index(max(scores))
        weakest = scores.index(min(scores))

        # 如果差距过大（>30%），交换 2 张
        if max(scores) > min(scores) * 1.3:
            swap_count = min(4, len(hands[strongest]))
            # 从最强手里随机抽几张非癞子牌给最弱
            for _ in range(swap_count):
                if hands[strongest]:
                    idx = random.randint(0, len(hands[strongest]) - 1)
                    card = hands[strongest].pop(idx)
                    hands[weakest].append(card)

    return hands


# ═══════════════════════════════════════════════════════
#  主模拟
# ═══════════════════════════════════════════════════════

def simulate(rounds: int, level: str = "2",
             control: str = "compensate"):
    deck = build_deck(level)
    laizi_pool = [(s, r, True) for s, r, lz in deck if lz]
    natural_pool = [(s, r) for s, r, lz in deck if not lz]

    # 累积统计
    all_evals = []       # [[eval_p1, eval_p2, ...], ...]
    score_by_nlz = defaultdict(list)
    player_scores = [[] for _ in range(PLAYERS)]

    for _ in range(rounds):
        laizi_counts = alloc_laizi()
        random.shuffle(laizi_pool)
        random.shuffle(natural_pool)
        hands = deal_controlled(natural_pool, laizi_counts, control=control)

        evals = []
        for i in range(PLAYERS):
            ev = evaluate_hand_v2(hands[i], laizi_counts[i])
            evals.append(ev)
            score_by_nlz[laizi_counts[i]].append(ev["score"])
            player_scores[i].append(ev["score"])
        all_evals.append(evals)

    return score_by_nlz, player_scores, all_evals


# ═══════════════════════════════════════════════════════
#  报告
# ═══════════════════════════════════════════════════════

def print_report(rounds: int = 30000):
    print(f"{'='*70}")
    print(f"  八红桃掼蛋 — 全手牌发牌控制模拟 v2")
    print(f"  癞子：{'→'.join(f'{lo}-{hi}={w}%' for lo, hi, w in TIERS)}")
    print(f"  复用计分：同一癞子可同时计入炸弹、同花顺、对子等")
    print(f"{'='*70}")

    for control in ["none", "compensate"]:
        score_by_nlz, player_scores, all_evals = simulate(rounds, control=control)
        label = "完全随机" if control == "none" else "补偿控制（强→弱调牌）"
        print(f"\n  {'▼'*50}")
        print(f"  策略：{label}  |  {rounds:,} 局")
        print(f"  {'▼'*50}")

        # 癞子 → 分数
        print(f"\n  {'癞子数':>6}  {'占比':>7}  {'均分':>7}  {'炸弹(均)':>9}  {'同花顺(均)':>9}  {'对子(均)':>8}")
        all_metrics = defaultdict(list)
        for evals in all_evals:
            for ev in evals:
                all_metrics[ev["nlz"]].append(ev)

        for nlz in sorted(all_metrics.keys()):
            metrics = all_metrics[nlz]
            pct = len(metrics) / (rounds * PLAYERS) * 100
            avg_score = sum(m["score"] for m in metrics) / len(metrics)
            avg_bombs = sum(m["bombs"] for m in metrics) / len(metrics)
            avg_flush = sum(m["flushes"] for m in metrics) / len(metrics)
            avg_pairs = sum(m["pairs"] for m in metrics) / len(metrics)
            print(f"  {nlz:>6}  {pct:>6.2f}%  {avg_score:>7.0f}  {avg_bombs:>9.2f}  {avg_flush:>9.2f}  {avg_pairs:>8.1f}")

        # 玩家均衡性
        print(f"\n  {'玩家':>6}  {'均分':>7}  {'最低':>7}  {'最高':>7}  {'标准差':>7}")
        for i in range(PLAYERS):
            scores = player_scores[i]
            avg = sum(scores) / len(scores)
            sd = (sum((s - avg) ** 2 for s in scores) / len(scores)) ** 0.5
            print(f"  P{i+1:>5}  {avg:>7.0f}  {min(scores):>7}  {max(scores):>7}  {sd:>7.0f}")

    # ── 单局演示 ──
    print(f"\n{'='*70}")
    print(f"  10 局演示（补偿模式）")
    print(f"{'='*70}")

    deck = build_deck("2")
    laizi_pool = [(s, r, True) for s, r, lz in deck if lz]
    natural_pool = [(s, r) for s, r, lz in deck if not lz]

    for game in range(10):
        laizi_counts = alloc_laizi()
        random.shuffle(natural_pool)
        hands = deal_controlled(natural_pool, laizi_counts, control="compensate")

        print(f"\n  第 {game+1} 局  癞子分配: {laizi_counts}")
        print(f"  {'玩家':>6}  {'癞子':>4}  {'炸弹':>4}  {'同花顺':>4}  {'对子':>4}  {'三张':>4}  {'钢板':>4}  {'大王':>4}  {'小王':>4}  {'得分':>6}")
        for i in range(PLAYERS):
            ev = evaluate_hand_v2(hands[i], laizi_counts[i])
            print(f"  P{i+1:>5}  {ev['nlz']:>4}  {ev['bombs']:>4}  {ev['flushes']:>4}  "
                  f"{ev['pairs']:>4}  {ev['triples']:>4}  {ev['steels']:>4}  "
                  f"{ev['big_joker']:>4}  {ev['small_joker']:>4}  {ev['score']:>6}")

        # 差距
        scores = [evaluate_hand_v2(hands[i], laizi_counts[i])["score"] for i in range(PLAYERS)]
        spread = max(scores) - min(scores)
        print(f"  {'强弱差':>6}  {'':>4}  {'':>4}  {'':>4}  {'':>4}  {'':>4}  {'':>4}  {'':>4}  {'':>4}  {spread:>6}")


if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
    print_report(rounds)
