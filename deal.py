#!/usr/bin/env python3
"""
八红桃掼蛋 发牌模拟器
========================
经典掼蛋：2 张级牌当万能牌（癞子）
八红桃：8 张级牌全部当万能牌（癞子）

发牌控制机制：按概率分配癞子给 4 个玩家，严格控制每人数量。
"""

import random
import json
import sys
from collections import Counter
from typing import Literal

# ─── 配置 ─────────────────────────────────────────────
TIERS = [
    # (min, max, 权重)  — 权重会被归一化为概率
    (1, 2, 69),
    (3, 4, 20),
    (5, 7, 10),
    (8, 8, 1),
]
TOTAL_LAIZI = 8          # 癞子总数
PLAYERS = 4              # 玩家数
SURPLUS_MODE: Literal["fewest", "random"] = "fewest"


def roll_tier() -> tuple[int, int]:
    """按配置概率随机选档位，返回 (min, max)"""
    weights = [t[2] for t in TIERS]
    tier = random.choices(TIERS, weights=weights, k=1)[0]
    return tier[0], tier[1]


def deal_once(mode: Literal["fewest", "random"] = "fewest") -> list[int]:
    """
    执行一次发牌，返回 4 个玩家的癞子数量列表 [p1, p2, p3, p4]

    - 每个玩家依次摇档位 → 档位内随机精确值 → 从库存扣
    - 全部摇完后，剩余癞子按 mode 分配
    """
    pool = TOTAL_LAIZI
    counts = [0] * PLAYERS

    # ── 第一阶段：依次摇癞子 ──
    for i in range(PLAYERS):
        if pool <= 0:
            break  # 库存耗尽，后续玩家拿 0 张

        lo, hi = roll_tier()
        # 档位内随机精确值，上限不能超过剩余库存
        hi = min(hi, pool)
        lo = min(lo, hi)  # 如果库存连 lo 都不够
        if lo > hi:
            lo = hi
        if lo == 0 and hi == 0:
            continue

        n = random.randint(lo, hi)
        counts[i] = n
        pool -= n

    # ── 第二阶段：处理剩余癞子 ──
    if pool > 0:
        if mode == "fewest":
            # 逐张分给癞子最少的人，平手随机
            for _ in range(pool):
                min_val = min(counts)
                candidates = [j for j, c in enumerate(counts) if c == min_val]
                winner = random.choice(candidates)
                counts[winner] += 1
        else:
            # 随机分配，每次随机挑一个人
            for _ in range(pool):
                counts[random.randint(0, PLAYERS - 1)] += 1

    return counts


def simulate(rounds: int = 100000, mode: Literal["fewest", "random"] = "fewest"):
    """批量模拟并统计"""
    dist: Counter = Counter()
    player_dists = [Counter() for _ in range(PLAYERS)]
    surplus_counts: Counter = Counter()

    for _ in range(rounds):
        counts = deal_once(mode)
        dist[tuple(counts)] += 1
        for i, c in enumerate(counts):
            player_dists[i][c] += 1

        total = sum(counts)
        surplus = total - TOTAL_LAIZI  # should always be 0 after correct allocation
        if surplus > 0:
            surplus_counts[surplus] += 1

    return dist, player_dists, surplus_counts


def print_report(rounds: int, mode: str):
    """打印统计报告"""
    dist, player_dists, surplus_counts = simulate(rounds, mode)

    mode_name = "补充少数" if mode == "fewest" else "随机分配"
    print(f"{'='*60}")
    print(f"  八红桃掼蛋 — 发牌模拟报告")
    print(f"  模拟次数：{rounds:,}  |  癞子总数：{TOTAL_LAIZI}  |  剩余处理：{mode_name}")
    print(f"{'='*60}")

    if surplus_counts:
        print(f"\n  ⚠️  出现盈余异常：{dict(surplus_counts)}")

    # ── 每个玩家的癞子分布 ──
    print(f"\n  {'─'*50}")
    print(f"  各玩家癞子数量分布")
    print(f"  {'─'*50}")
    print(f"  {'癞子数':>6}  {'玩家1':>8}  {'玩家2':>8}  {'玩家3':>8}  {'玩家4':>8}")
    for n in range(0, TOTAL_LAIZI + 1):
        row = f"  {n:>6}"
        for i in range(PLAYERS):
            row += f"  {player_dists[i].get(n, 0) / rounds * 100:>7.2f}%"
        print(row)

    # ── 整体分布（排序后） ──
    print(f"\n  {'─'*50}")
    print(f"  Top 20 癞子组合（按出现次数降序）")
    print(f"  {'─'*50}")
    print(f"  {'组合 (P1,P2,P3,P4)':>22}  {'次数':>8}  {'占比'}")
    for combo, cnt in dist.most_common(20):
        pct = cnt / rounds * 100
        bar = "█" * int(pct * 2)
        print(f"  {str(combo):>22}  {cnt:>8}  {pct:>6.2f}%  {bar}")

    # ── 统计摘要 ──
    print(f"\n  {'─'*50}")
    print(f"  摘要统计")
    print(f"  {'─'*50}")

    all_counts = []
    for i in range(PLAYERS):
        vals = [c for c, n in player_dists[i].items() for _ in range(n)]
        avg = sum(vals) / len(vals) if vals else 0
        all_counts.extend(vals)
        print(f"  玩家{i+1}: 均值={avg:.2f}  最小={min(vals)}  最大={max(vals)}")

    # 单人拿 ≥5 癞子的概率
    for i in range(PLAYERS):
        high = sum(n for c, n in player_dists[i].items() if c >= 5)
        print(f"  玩家{i+1} 拿 ≥5 癞子: {high / rounds * 100:.2f}%")

    print()


def main():
    rounds = 100000
    if len(sys.argv) > 1:
        rounds = int(sys.argv[1])

    for mode in ("fewest", "random"):
        print_report(rounds, mode)

    # ── 单次演示 ──
    print(f"\n{'='*60}")
    print(f"  10 次发牌演示（剩余处理：补充少数）")
    print(f"{'='*60}")
    for i in range(10):
        counts = deal_once("fewest")
        bars = " ".join("█" * c if c > 0 else "·" for c in counts)
        print(f"  #{i+1:>2}  {counts}  sum={sum(counts)}  {bars}")


if __name__ == "__main__":
    main()
