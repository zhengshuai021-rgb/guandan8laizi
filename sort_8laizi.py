#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八癞子掼蛋 — 一键理牌算法
============================
基于普通掼蛋一键理牌逻辑（k7.game.gd.sort），适配八癞子（8张万能牌）玩法。

核心差异：
  - 普通掼蛋：2张癞子 -> 5种方案 + 24排列（顺子/木板/钢板/三带二）
  - 八癞子：8张癞子 -> 需额外探索「癞子在炸弹 vs 同花顺之间的分配比例」

算法流程：
  1. 分离癞子牌与自然牌
  2. 5种基础策略 x 24排列 x (0~n_lz)癞子分配
  3. 每种策略：王炸 -> 同花顺/炸弹 -> 顺子/木板/钢板/三带二 -> 三张/对子/单张
  4. 按"单张最少优先"规则挑选最优方案
  5. 返回 (bombs, others)
"""

import sys
import random
import itertools
from collections import Counter, defaultdict
from typing import Optional

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ================================================================
#  常量
# ================================================================

SUITS = ["S", "H", "C", "D"]  # Spade, Heart, Club, Diamond
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

# rank -> value for power calculation
RANK_VALUE = {}
for r in RANKS:
    if r == "A":
        RANK_VALUE[r] = 14
    elif r == "J":
        RANK_VALUE[r] = 11
    elif r == "Q":
        RANK_VALUE[r] = 12
    elif r == "K":
        RANK_VALUE[r] = 13
    else:
        RANK_VALUE[r] = int(r)

RANK_ORDER = {r: i for i, r in enumerate(RANKS)}  # A=0, 2=1, ..., K=12

FOUR_KING_POWER = 1017
WILD_POWER = 15
SMALL_JOKER_POWER = 16
BIG_JOKER_POWER = 17


# ================================================================
#  数据结构
# ================================================================

_card_cid_counter = 0

class Card:
    __slots__ = ('suit', 'rank', 'is_wild', 'power', 'value', 'cid')

    def __init__(self, suit: str, rank: str, is_wild: bool = False, cid: int = None):
        global _card_cid_counter
        self.suit = suit
        self.rank = rank
        self.is_wild = is_wild
        if cid is not None:
            self.cid = cid
        else:
            _card_cid_counter += 1
            self.cid = _card_cid_counter
        if is_wild:
            self.power = WILD_POWER
            self.value = 1
        elif rank == "SJ":  # Small Joker
            self.power = SMALL_JOKER_POWER
            self.value = 16
        elif rank == "BJ":  # Big Joker
            self.power = BIG_JOKER_POWER
            self.value = 17
        else:
            self.value = RANK_VALUE.get(rank, 0)
            self.power = self.value

    def __repr__(self):
        if self.is_wild:
            return "W"
        if self.rank in ("SJ", "BJ"):
            return self.rank
        return f"{self.suit}{self.rank}"

    def __eq__(self, other):
        return self.cid == other.cid

    def __hash__(self):
        return hash(self.cid)


class CardGroup:
    __slots__ = ('cards', 'group_type', 'power', 'size')

    def __init__(self, cards: list, group_type: str, power: int = 0):
        self.cards = list(cards)
        self.group_type = group_type
        self.power = power
        self.size = len(self.cards)

    def __repr__(self):
        return f"[{self.group_type}:{self.size}] {' '.join(str(c) for c in self.cards)}"

    @property
    def wild_count(self) -> int:
        return sum(1 for c in self.cards if c.is_wild)

    def first_natural_power(self) -> int:
        for c in self.cards:
            if not c.is_wild:
                return c.power
        return self.cards[0].power if self.cards else 0

    def sort_power(self) -> int:
        """组间排序权值 = 首非癞子power + 前置癞子偏移"""
        for i, c in enumerate(self.cards):
            if not c.is_wild:
                return c.power + i
        return self.cards[0].power if self.cards else 0


# ================================================================
#  工具函数
# ================================================================

def is_natural_rank(c: Card) -> bool:
    return (not c.is_wild) and c.rank not in ("SJ", "BJ")


def rank_counts(cards: list) -> dict:
    cnt = {}
    for c in cards:
        if is_natural_rank(c):
            cnt[c.rank] = cnt.get(c.rank, 0) + 1
    return cnt


def suit_groups(cards: list) -> dict:
    groups = defaultdict(list)
    for c in cards:
        if is_natural_rank(c):
            groups[c.suit].append(c)
    return groups


def naturals_only(cards: list) -> list:
    return [c for c in cards if not c.is_wild]


def wilds_only(cards: list) -> list:
    return [c for c in cards if c.is_wild]


# ================================================================
#  王炸提取
# ================================================================

def extract_king_bombs(pool: list) -> list:
    """提取王炸：4张大小王=1个王炸"""
    jokers = [c for c in pool if c.rank in ("SJ", "BJ") and not c.is_wild]
    if len(jokers) >= 4:
        taken = jokers[:4]
        for c in taken:
            pool.remove(c)
        return [CardGroup(taken, "king", FOUR_KING_POWER)]
    return []


# ================================================================
#  炸弹提取
# ================================================================

def extract_bombs(pool: list, wild_pool: list,
                  max_wilds_for_bombs: int = 999) -> list:
    """
    贪心提炸弹。从 pool(自然牌) 和 wild_pool(癞子) 中消耗。
    max_wilds_for_bombs: 最多用多少个癞子做炸弹。
    
    策略：
      Phase 0: 提取纯自然炸弹（>=4张同一rank，不消耗癞子）
      Phase 1: 用癞子补足 3张->4线, 2张->4线, 1张->4线
      Phase 2: 已有4+张的自然炸弹 + 癞子 = 更大炸弹
      Phase 3: 纯癞子炸弹（4个癞子=1个炸弹）
    """
    rank_cnt = rank_counts(pool)
    remaining = min(max_wilds_for_bombs, len(wild_pool))
    bombs = []

    # Phase 0: 纯自然炸弹（>=4张，不消耗癞子）
    for rank in sorted(rank_cnt.keys(), key=lambda r: (-rank_cnt[r], -RANK_VALUE.get(r, 0))):
        cnt = rank_cnt[rank]
        if cnt >= 4:
            cards_nat = [c for c in pool if c.rank == rank and is_natural_rank(c)][:cnt]
            for c in cards_nat:
                pool.remove(c)
            rank_cnt[rank] = 0
            power = cards_nat[0].power + cnt * 100
            bombs.append(CardGroup(cards_nat, "bomb", power))

    # Phase 1: 补足 3张->4线, 2张->4线, 1张->4线
    for need_nat in [3, 2, 1]:
        for rank in sorted(rank_cnt.keys(), key=lambda r: (-rank_cnt[r], -RANK_VALUE.get(r, 0))):
            cnt = rank_cnt[rank]
            if cnt != need_nat:
                continue
            need = 4 - cnt
            if need <= remaining:
                cards_nat = [c for c in pool if c.rank == rank and is_natural_rank(c)][:cnt]
                for c in cards_nat:
                    pool.remove(c)
                w = wild_pool[:need]
                del wild_pool[:need]
                remaining -= need
                rank_cnt[rank] = 0
                power = cards_nat[0].power + 4 * 100
                bombs.append(CardGroup(w + cards_nat, "bomb", power))

    # Phase 2: 已有4+张的rank，每加1癞子=多1线
    rank_cnt = rank_counts(pool)
    for rank in sorted(rank_cnt.keys(), key=lambda r: (-rank_cnt[r], -RANK_VALUE.get(r, 0))):
        cnt = rank_cnt[rank]
        if cnt < 4 or remaining <= 0:
            continue
        add = min(remaining, cnt)
        cards_nat = [c for c in pool if c.rank == rank and is_natural_rank(c)][:cnt]
        for c in cards_nat:
            pool.remove(c)
        w = wild_pool[:add]
        del wild_pool[:add]
        remaining -= add
        total = cnt + add
        power = cards_nat[0].power + total * 100
        bombs.append(CardGroup(w + cards_nat, "bomb", power))
        rank_cnt[rank] = 0

    # Phase 3: 纯癞子炸弹（4个癞子=1个炸弹）
    while remaining >= 4:
        w = wild_pool[:4]
        del wild_pool[:4]
        remaining -= 4
        bombs.append(CardGroup(w, "bomb", WILD_POWER + 4 * 100))

    return bombs


# ================================================================
#  同花顺提取（严格5张同花色连续牌）
# ================================================================

def extract_flush_straights(pool: list, wild_pool: list,
                            max_wilds_for_flush: int = 999,
                            suit_priority: list = None,
                            max_flushes: int = 999) -> list:
    """
    贪心提同花顺：严格 5 张同花色连续牌，癞子补断口。
    支持 A 作为高牌（10-J-Q-K-A）。
    max_wilds_for_flush: 最多用多少个癞子做同花顺。
    suit_priority: 花色处理顺序，None 则使用 SUITS 默认顺序。
    max_flushes: 最多创建几个同花顺。
    """
    remaining = min(max_wilds_for_flush, len(wild_pool))
    flushes = []
    suit_map = suit_groups(pool)

    # 所有可能的 5 卡连续窗口：
    #   A,2,3,4,5  (索引 0-4)  ~  9,10,J,Q,K  (索引 8-12)  = 9 个普通窗口
    #   10,J,Q,K,A  (索引 9-13, A 视为高牌索引 13) = 1 个 A 高牌窗口
    WINDOWS = [(s, s + 4, False) for s in range(9)] + [(9, 13, True)]

    if suit_priority is None:
        suit_order = SUITS
    else:
        suit_order = suit_priority

    # 当 max_flushes=1 时：全局搜索最优单个同花顺（跨花色按威力+癞子数选）
    if max_flushes == 1:
        candidates = []  # (suit, start, wilds_needed, ace_high)
        for suit in SUITS:
            cards = suit_map.get(suit, [])
            if not cards:
                continue
            suit_ranks = set(RANK_ORDER[c.rank] for c in cards)
            for start, end, ace_high in WINDOWS:
                wilds = 0
                if ace_high:
                    for ri in range(start, 13):
                        if ri not in suit_ranks:
                            wilds += 1
                    if 0 not in suit_ranks:
                        wilds += 1
                else:
                    for ri in range(start, end + 1):
                        if ri not in suit_ranks:
                            wilds += 1
                if wilds <= remaining:
                    candidates.append((suit, start, wilds, ace_high))
        if candidates:
            # 优先高位、同高位选癞子最少的
            candidates.sort(key=lambda x: (-x[1], x[2]))
            best_suit, best_start, best_wilds, best_ace = candidates[0]
            suit_order = [best_suit]

    for suit in suit_order:
        if remaining <= 0:
            break
        cards = suit_map.get(suit, [])
        if not cards:
            continue

        suit_ranks = set(RANK_ORDER[c.rank] for c in cards)

        best = None  # (start_idx, wilds_needed, ace_high)

        for start, end, ace_high in WINDOWS:
            wilds = 0
            if ace_high:
                for ri in range(start, 13):
                    if ri not in suit_ranks:
                        wilds += 1
                if 0 not in suit_ranks:
                    wilds += 1
            else:
                for ri in range(start, end + 1):
                    if ri not in suit_ranks:
                        wilds += 1

            if wilds <= remaining:
                # 优先选高位同花顺（start越大=点数越高），同起点时选癞子少的
                if best is None or start > best[0] or (
                    start == best[0] and wilds < best[1]
                ):
                    best = (start, wilds, ace_high)

        if best is None:
            continue

        start, wilds_needed, ace_high = best

        # 收集该花色在该窗口内的自然牌（每 rank 最多取 1 张）
        taken = []
        if ace_high:
            for ri in range(start, 13):
                for c in list(pool):
                    if (c.suit == suit and is_natural_rank(c)
                            and RANK_ORDER.get(c.rank) == ri):
                        taken.append(c)
                        pool.remove(c)
                        break  # 每 rank 只取 1 张
            for c in list(pool):
                if c.suit == suit and c.rank == "A" and is_natural_rank(c):
                    taken.append(c)
                    pool.remove(c)
                    break
        else:
            end = start + 4
            for ri in range(start, end + 1):
                for c in list(pool):
                    if (c.suit == suit and is_natural_rank(c)
                            and RANK_ORDER.get(c.rank) == ri):
                        taken.append(c)
                        pool.remove(c)
                        break  # 每 rank 只取 1 张

        # 消耗癞子
        w = wild_pool[:wilds_needed]
        del wild_pool[:wilds_needed]
        remaining -= wilds_needed

        all_cards = w + taken
        first_nat_idx = next((i for i, c in enumerate(all_cards) if not c.is_wild), 0)
        fnp = all_cards[first_nat_idx].value
        power = 520 + fnp + max(0, 4 - first_nat_idx)
        flushes.append(CardGroup(all_cards, "flush", power))
        if len(flushes) >= max_flushes:
            break

    return flushes


# ================================================================
#  顺子提取
# ================================================================

def extract_straights(pool: list, wild_pool: list) -> list:
    """贪心提顺子（恰好5张连续，不限花色）。用癞子补断口。
    掼蛋顺子只能是5连张。支持A作为高牌（10-J-Q-K-A）。"""
    straights = []
    rank_cnt = rank_counts(pool)
    n_wilds = len(wild_pool)

    while True:
        available = sorted(
            (r for r, c in rank_cnt.items() if c > 0),
            key=lambda r: RANK_ORDER[r]
        )
        if len(available) < 2:
            break

        # 搜索所有恰好5连续的窗口（普通 + Ace高牌）
        best = None  # (start_idx, needed_wilds, is_ace_high)

        # 普通5连
        for i in range(len(available)):
            start_r = available[i]
            si = RANK_ORDER[start_r]
            if si > 8:  # 最高从9开始(9,J,Q,K,?)，10开始只能到A(高牌)
                # 尝试A高牌：10-J-Q-K-A
                if si == 9:  # 10
                    needed = sum(1 for ri in [9,10,11,12] if rank_cnt.get(RANKS[ri], 0) == 0)
                    if rank_cnt.get("A", 0) == 0:
                        needed += 1
                    if needed <= n_wilds:
                        if best is None or needed < best[1]:
                            best = (i, needed, True)
                continue
            # 需要 available 中有连续的5个rank
            ranks_needed = [RANKS[si + j] for j in range(5)]
            # 检查这5个rank是否都在available中（用rank_cnt检查，因为有癞子可以补）
            needed = sum(1 for r in ranks_needed if rank_cnt.get(r, 0) == 0)
            if needed <= n_wilds:
                if best is None or needed < best[1]:
                    best = (i, needed, False)

        if best is None:
            break

        si, needed, is_ace_high = best
        start_r = available[si]
        si_idx = RANK_ORDER[start_r]

        # 提取
        taken = []
        if is_ace_high:
            for ri in [9, 10, 11, 12]:  # 10, J, Q, K
                r = RANKS[ri]
                if rank_cnt.get(r, 0) > 0:
                    c = next((x for x in pool if x.rank == r and is_natural_rank(x)), None)
                    if c:
                        pool.remove(c)
                        taken.append(c)
                        rank_cnt[r] = max(0, rank_cnt[r] - 1)
            if rank_cnt.get("A", 0) > 0:
                c = next((x for x in pool if x.rank == "A" and is_natural_rank(x)), None)
                if c:
                    pool.remove(c)
                    taken.append(c)
                    rank_cnt["A"] = max(0, rank_cnt["A"] - 1)
        else:
            for ri in range(si_idx, si_idx + 5):
                r = RANKS[ri]
                if rank_cnt.get(r, 0) > 0:
                    c = next((x for x in pool if x.rank == r and is_natural_rank(x)), None)
                    if c:
                        pool.remove(c)
                        taken.append(c)
                        rank_cnt[r] = max(0, rank_cnt[r] - 1)

        w = wild_pool[:needed]
        del wild_pool[:needed]
        n_wilds -= needed

        power = taken[0].power + 4 if taken else 0
        straights.append(CardGroup(w + taken, "straight", power))

    return straights


# ================================================================
#  木板提取（连对：恰好3个连续rank各有>=2张）
# ================================================================

def extract_boards(pool: list, wild_pool: list) -> list:
    """贪心提木板（连对：恰好3个连续rank各>=2张）。掼蛋木板只能是3连对。
    支持用癞子补到2张（即某rank可以0自然牌+2癞子组对）。"""
    boards = []
    rank_cnt = rank_counts(pool)
    n_wilds = len(wild_pool)

    while True:
        # 包含所有可能组对的rank（含纯癞子对）
        available = sorted(
            (r for r in RANKS if rank_cnt.get(r, 0) + n_wilds >= 2),
            key=lambda r: RANK_ORDER[r]
        )
        if len(available) < 3:
            break

        # 扫描所有恰好3连续的窗口，选癞子需求最少的；同等需求选高rank的
        best = None  # (start_i, needed_wilds, end_rank_order)
        for i in range(len(available) - 2):
            r0, r1, r2 = available[i], available[i+1], available[i+2]
            if RANK_ORDER[r1] != RANK_ORDER[r0] + 1 or RANK_ORDER[r2] != RANK_ORDER[r0] + 2:
                continue
            needed = sum(max(0, 2 - rank_cnt.get(available[i+j], 0)) for j in range(3))
            if needed <= n_wilds:
                end_order = RANK_ORDER[available[i+2]]
                if best is None or needed < best[1] or (needed == best[1] and end_order > best[2]):
                    best = (i, needed, end_order)

        # 尝试 Ace-high 木板: Q-K-A (RANK_ORDER A=0, 但作为高牌跟在K后)
        if best is None:
            ace_needed = sum(max(0, 2 - rank_cnt.get(r, 0)) for r in ["Q", "K", "A"])
            if ace_needed <= n_wilds and all(
                rank_cnt.get(r, 0) + n_wilds >= 2 for r in ["Q", "K", "A"]
            ):
                best = (-1, ace_needed, 0)  # special marker for ace-high

        if best is None:
            break

        si, needed, _ = best
        ranks_to_take = []
        if si == -1:
            ranks_to_take = ["Q", "K", "A"]
        else:
            ranks_to_take = [available[si+j] for j in range(3)]

        taken = []
        for r in ranks_to_take:
            cnt = rank_cnt.get(r, 0)
            cards = [c for c in pool if c.rank == r and is_natural_rank(c)][:min(cnt, 2)]
            for c in cards:
                pool.remove(c)
                taken.append(c)
                rank_cnt[r] = max(0, rank_cnt[r] - 1)

        w = wild_pool[:needed]
        del wild_pool[:needed]
        n_wilds -= needed

        power = taken[0].power if taken else WILD_POWER
        boards.append(CardGroup(w + taken, "board", power))

    return boards


# ================================================================
#  钢板提取（恰好2个连续rank各有>=3张）
# ================================================================

def extract_steel_plates(pool: list, wild_pool: list) -> list:
    """贪心提钢板（恰好2个连续rank各>=3张）。掼蛋钢板只能是2连三张。
    支持用癞子补到3张（即某rank可以0自然牌+3癞子组三张）。"""
    steels = []
    rank_cnt = rank_counts(pool)
    n_wilds = len(wild_pool)

    while True:
        # 包含所有可能组三张的rank（含纯癞子三张）
        available = sorted(
            (r for r in RANKS if rank_cnt.get(r, 0) + n_wilds >= 3),
            key=lambda r: RANK_ORDER[r]
        )
        if len(available) < 2:
            break

        # 扫描所有恰好2连续的窗口，选癞子需求最少的；同等需求选高rank的
        best = None  # (start_i, needed_wilds, end_rank_order)
        for i in range(len(available) - 1):
            r0, r1 = available[i], available[i+1]
            if RANK_ORDER[r1] != RANK_ORDER[r0] + 1:
                continue
            needed = sum(max(0, 3 - rank_cnt.get(available[i+j], 0)) for j in range(2))
            if needed <= n_wilds:
                end_order = RANK_ORDER[available[i+1]]
                if best is None or needed < best[1] or (needed == best[1] and end_order > best[2]):
                    best = (i, needed, end_order)

        # 尝试 Ace-high 钢板: K-A (RANK_ORDER A=0, K=12)
        if best is None:
            ace_needed = sum(max(0, 3 - rank_cnt.get(r, 0)) for r in ["K", "A"])
            if ace_needed <= n_wilds and all(
                rank_cnt.get(r, 0) + n_wilds >= 3 for r in ["K", "A"]
            ):
                best = (-1, ace_needed, 0)  # special marker for ace-high

        if best is None:
            break

        si, needed, _ = best
        ranks_to_take = []
        if si == -1:
            ranks_to_take = ["K", "A"]
        else:
            ranks_to_take = [available[si+j] for j in range(2)]

        taken = []
        for r in ranks_to_take:
            cnt = rank_cnt.get(r, 0)
            cards = [c for c in pool if c.rank == r and is_natural_rank(c)][:min(cnt, 3)]
            for c in cards:
                pool.remove(c)
                taken.append(c)
                rank_cnt[r] = max(0, rank_cnt[r] - 1)

        w = wild_pool[:needed]
        del wild_pool[:needed]
        n_wilds -= needed

        power = taken[0].power if taken else WILD_POWER
        group = CardGroup(w + taken, "steel", power)

        natural_ranks = sorted(set(c.rank for c in taken if is_natural_rank(c)),
                               key=lambda r: RANK_ORDER[r])
        for i in range(1, len(natural_ranks)):
            if RANK_ORDER[natural_ranks[i]] != RANK_ORDER[natural_ranks[i-1]] + 1:
                raise ValueError(f"Steel plate has non-consecutive ranks: {natural_ranks}")

        steels.append(group)

    return steels


# ================================================================
#  三带二提取
# ================================================================

def extract_three_with_two(pool: list, wild_pool: list,
                           max_pair_value: int = 11) -> list:
    """贪心提三带二（三张+对子，对子点数<=max_pair_value即不超过J）。"""
    twt_list = []
    rank_cnt = rank_counts(pool)
    n_wilds = len(wild_pool)

    while True:
        # 找可用的三张rank
        triple_candidates = sorted(
            [(r, c) for r, c in rank_cnt.items() if c + n_wilds >= 3 and c > 0],
            key=lambda x: (-x[1], -RANK_VALUE.get(x[0], 0))
        )
        if not triple_candidates:
            break

        tr, tcnt = triple_candidates[0]
        need_triple = max(0, 3 - tcnt)

        # 找可用的对子rank（排除三张rank自身，除非cnt>3）
        # 优先选恰好2张的rank（不浪费），再从小牌开始带（小对子先用）
        pair_candidates = sorted(
            [(r, c) for r, c in rank_cnt.items()
             if c + n_wilds >= 2 and c > 0
             and RANK_VALUE.get(r, 0) <= max_pair_value
             and (r != tr or c > 3)],
            key=lambda x: (0 if x[1] == 2 else 1, -x[1], RANK_VALUE.get(x[0], 0))
        )
        if not pair_candidates:
            break

        pr, pcnt = pair_candidates[0]
        need_pair = max(0, 2 - pcnt)

        total_need = need_triple + need_pair
        if total_need > n_wilds:
            break

        # 提取三张
        triple_cards = [c for c in pool if c.rank == tr and is_natural_rank(c)][:tcnt]
        for c in triple_cards:
            pool.remove(c)
            rank_cnt[tr] = max(0, rank_cnt[tr] - 1)

        # 提取对子（最多2张）
        pair_cards = [c for c in pool if c.rank == pr and is_natural_rank(c)][:min(pcnt, 2)]
        for c in pair_cards:
            pool.remove(c)
            rank_cnt[pr] = max(0, rank_cnt[pr] - 1)

        triple_wilds = wild_pool[:need_triple]
        del wild_pool[:need_triple]
        pair_wilds = wild_pool[:need_pair]
        del wild_pool[:need_pair]
        n_wilds -= total_need

        power = triple_cards[0].power if triple_cards else (
            triple_wilds[0].power if triple_wilds else WILD_POWER
        )
        twt_list.append(CardGroup(triple_wilds + triple_cards + pair_wilds + pair_cards,
                                  "three_two", power))

    return twt_list


# ================================================================
#  三张/对子/单张提取
# ================================================================

def extract_remaining(pool: list, wild_pool: list) -> tuple:
    """从剩余牌中提取三张、对子、单张。"""
    triples = []
    pairs = []
    singles = []
    rank_cnt = rank_counts(pool)

    # 三张
    for rank in sorted(rank_cnt.keys(), key=lambda r: (-rank_cnt[r], -RANK_VALUE.get(r, 0))):
        cnt = rank_cnt[rank]
        while cnt + len(wild_pool) >= 3 and cnt >= 1:
            take = min(cnt, 3)
            cards = [c for c in pool if c.rank == rank and is_natural_rank(c)][:take]
            need = 3 - take
            for c in cards:
                pool.remove(c)
                rank_cnt[rank] = max(0, rank_cnt[rank] - 1)
                cnt -= 1
            w = wild_pool[:need]
            del wild_pool[:need]
            triples.append(CardGroup(w + cards, "triple",
                                     cards[0].power if cards else WILD_POWER))

    # 对子
    rank_cnt = rank_counts(pool)
    for rank in sorted(rank_cnt.keys(), key=lambda r: (-rank_cnt[r], -RANK_VALUE.get(r, 0))):
        cnt = rank_cnt[rank]
        while cnt + len(wild_pool) >= 2 and cnt >= 1:
            take = min(cnt, 2)
            cards = [c for c in pool if c.rank == rank and is_natural_rank(c)][:take]
            need = 2 - take
            for c in cards:
                pool.remove(c)
                rank_cnt[rank] = max(0, rank_cnt[rank] - 1)
                cnt -= 1
            w = wild_pool[:need]
            del wild_pool[:need]
            pairs.append(CardGroup(w + cards, "pair",
                                   cards[0].power if cards else WILD_POWER))

    # 大小王对子（2小王=对子，2大王=对子）
    for joker_rank in ("SJ", "BJ"):
        jokers = [c for c in pool if c.rank == joker_rank]
        while len(jokers) >= 2:
            take = jokers[:2]
            for c in take:
                pool.remove(c)
            pairs.append(CardGroup(take, "pair", take[0].power))
            jokers = jokers[2:]

    # 单张（剩余所有）
    for c in list(pool):
        pool.remove(c)
        singles.append(CardGroup([c], "single", c.power))
    for c in list(wild_pool):
        wild_pool.remove(c)
        singles.append(CardGroup([c], "single", c.power))

    return triples, pairs, singles


# ================================================================
#  方案结果
# ================================================================

class SortResult:
    __slots__ = ('kings', 'flushes', 'bombs', 'straights', 'boards',
                 'steels', 'three_with_twos', 'triples', 'pairs', 'singles')

    def __init__(self):
        self.kings = []
        self.flushes = []
        self.bombs = []
        self.straights = []
        self.boards = []
        self.steels = []
        self.three_with_twos = []
        self.triples = []
        self.pairs = []
        self.singles = []

    def score(self) -> tuple:
        bomb5plus = sum(1 for b in self.bombs if b.size >= 5)
        return (
            len(self.singles),           # 单张数（越小越好 — 减少手牌碎片化）
            -len(self.bombs),            # 炸弹数
            -len(self.flushes),          # 同花顺数
            -bomb5plus,                  # 5+线炸弹数
            -len(self.straights),        # 顺子数（同花顺 > 炸弹 > 顺子 > 钢板 > 木板 > 三带二）
            -len(self.steels),           # 钢板数
            -len(self.boards),           # 木板数
            -len(self.three_with_twos),  # 三带二数
            -len(self.triples),          # 三张数
            -len(self.pairs),            # 对子数
        )

    @property
    def bombs_output(self) -> list:
        """炸弹区：王炸 -> 同花顺 -> 5+线炸弹 -> 4线炸弹"""
        result = list(self.kings)
        result.extend(self.flushes)
        b5 = sorted([b for b in self.bombs if b.size >= 5],
                    key=lambda g: (-g.size, g.sort_power()))
        b4 = sorted([b for b in self.bombs if b.size == 4],
                    key=lambda g: g.sort_power())
        result.extend(b5)
        result.extend(b4)
        return result

    @property
    def others_output(self) -> list:
        """非炸弹区：顺子/木板/钢板/三带二/三张/对子/单张"""
        result = []
        result.extend(self.straights)
        result.extend(self.boards)
        result.extend(self.steels)
        result.extend(self.three_with_twos)
        result.extend(self.triples)
        result.extend(self.pairs)
        result.extend(self.singles)
        result.sort(key=lambda g: g.sort_power(), reverse=True)
        return result


# ================================================================
#  24种提取顺序（顺子/木板/钢板/三带二）
# ================================================================

EXTRACTION_ORDERS = list(itertools.permutations(
    ["straight", "board", "steel", "three_two"]
))


# ================================================================
#  执行一种策略
# ================================================================

def execute_strategy(natural_cards: list, wild_cards: list,
                     strategy: str, bomb_wilds: int,
                     extraction_order: tuple) -> SortResult:
    """
    执行一种理牌策略。
    
    strategy:
      "O_flush_first"  - 同花顺先于炸弹
      "N_bomb_first"   - 炸弹先于同花顺
    
    bomb_wilds: 给炸弹预留的癞子数量上限
    """
    pool = list(natural_cards)
    wp = list(wild_cards)
    n_lz = len(wp)

    result = SortResult()
    result.kings = extract_king_bombs(pool)

    if strategy in ("O_flush_first", "O_flush_single"):
        # 方案O: 同花顺 -> 炸弹
        max_f = 1 if strategy == "O_flush_single" else 999
        suit_counts = defaultdict(int)
        for c in pool:
            if is_natural_rank(c):
                suit_counts[c.suit] += 1
        flush_suit_order = sorted(SUITS, key=lambda s: suit_counts.get(s, 0))
        result.flushes = extract_flush_straights(pool, wp,
                                                 max_wilds_for_flush=max(0, len(wp) - bomb_wilds),
                                                 suit_priority=flush_suit_order,
                                                 max_flushes=max_f)
        result.bombs = extract_bombs(pool, wp, bomb_wilds)
    elif strategy == "N_bomb_first":
        # 方案N: 炸弹 -> 同花顺
        result.bombs = extract_bombs(pool, wp, bomb_wilds)
        result.flushes = extract_flush_straights(pool, wp)

    # 按顺序提取 顺子/木板/钢板/三带二
    for ext_type in extraction_order:
        if ext_type == "straight":
            result.straights = extract_straights(pool, wp)
        elif ext_type == "board":
            result.boards = extract_boards(pool, wp)
        elif ext_type == "steel":
            result.steels = extract_steel_plates(pool, wp)
        elif ext_type == "three_two":
            result.three_with_twos = extract_three_with_two(pool, wp)

    # 三张/对子/单张
    result.triples, result.pairs, result.singles = extract_remaining(pool, wp)

    return result


# ================================================================
#  主算法
# ================================================================

def try_all_strategies(natural_cards: list, wild_cards: list) -> SortResult:
    """枚举所有策略组合，返回最优"""
    n_lz = len(wild_cards)
    best = None

    # 方案O: 同花顺先于炸弹，尝试不同 bomb_wilds
    for bomb_wilds in range(n_lz + 1):
        for order in EXTRACTION_ORDERS:
            result = execute_strategy(
                list(natural_cards), list(wild_cards),
                "O_flush_first", bomb_wilds, order
            )
            if best is None or result.score() < best.score():
                best = result

    # 方案N: 炸弹先于同花顺
    for bomb_wilds in range(n_lz + 1):
        for order in EXTRACTION_ORDERS:
            result = execute_strategy(
                list(natural_cards), list(wild_cards),
                "N_bomb_first", bomb_wilds, order
            )
            if best is None or result.score() < best.score():
                best = result

    # 方案O (去掉顺子，即 order 中没有 "straight")
    orders_no_straight = [o for o in EXTRACTION_ORDERS if o[0] != "straight"]
    for bomb_wilds in range(n_lz + 1):
        for order in orders_no_straight:
            result = execute_strategy(
                list(natural_cards), list(wild_cards),
                "O_flush_first", bomb_wilds, order
            )
            if best is None or result.score() < best.score():
                best = result

    return best


def sort_8laizi(hand_cards: list) -> tuple:
    """
    八癞子一键理牌主入口。
    
    返回 (bombs, others):
      bombs  - 王炸 + 同花顺 + 炸弹（炸弹区）
      others - 顺子/木板/钢板/三带二/三张/对子/单张
    """
    wild_cards = wilds_only(hand_cards)
    natural_cards = naturals_only(hand_cards)
    best = try_all_strategies(natural_cards, wild_cards)

    if best is None:
        singles = [CardGroup([c], "single", c.power) for c in hand_cards]
        return ([], singles)

    return (best.bombs_output, best.others_output)


def sort_8laizi_with_details(hand_cards: list) -> dict:
    """
    八癞子一键理牌（含详情），供 Web UI 使用。
    
    返回 dict:
      all_results: 所有策略结果列表，每个包含 strategy/meta/score/stats/bombs/others/zones
      best_index: 最优结果在 all_results 中的索引
      bombs, others: 最优结果的 bombs/others
      zones: 最优结果的三区划分
    """
    wild_cards = wilds_only(hand_cards)
    natural_cards = naturals_only(hand_cards)
    n_lz = len(wild_cards)

    all_results = []

    def add_result(result: SortResult, meta: dict):
        bombs = result.bombs_output
        others = result.others_output
        sb, ns, sr = partition_for_display(bombs, others)

        all_results.append({
            "meta": meta,
            "score": list(result.score()),
            "stats": _result_stats(result),
            "bombs": _groups_to_dict(bombs),
            "others": _groups_to_dict(others),
            "zones": {
                "bombs": _groups_to_dict(sb),
                "notsort": _groups_to_dict(ns),
                "sortR": _groups_to_dict(sr),
            },
        })

    # 方案O: 同花顺先于炸弹
    for bomb_wilds in range(n_lz + 1):
        for order in EXTRACTION_ORDERS:
            result = execute_strategy(
                list(natural_cards), list(wild_cards),
                "O_flush_first", bomb_wilds, order
            )
            add_result(result, {
                "strategy": "O_flush_first",
                "bomb_wilds": bomb_wilds,
                "order": list(order),
            })

    # 方案O_single: 同花先但最多只做1个同花顺（避免贪心消耗过多自然牌）
    for bomb_wilds in range(n_lz + 1):
        for order in EXTRACTION_ORDERS:
            result = execute_strategy(
                list(natural_cards), list(wild_cards),
                "O_flush_single", bomb_wilds, order
            )
            add_result(result, {
                "strategy": "O_flush_single",
                "bomb_wilds": bomb_wilds,
                "order": list(order),
            })

    # 方案N: 炸弹先于同花顺
    for bomb_wilds in range(n_lz + 1):
        for order in EXTRACTION_ORDERS:
            result = execute_strategy(
                list(natural_cards), list(wild_cards),
                "N_bomb_first", bomb_wilds, order
            )
            add_result(result, {
                "strategy": "N_bomb_first",
                "bomb_wilds": bomb_wilds,
                "order": list(order),
            })

    # 方案O (去掉顺子)
    orders_no_straight = [o for o in EXTRACTION_ORDERS if o[0] != "straight"]
    for bomb_wilds in range(n_lz + 1):
        for order in orders_no_straight:
            result = execute_strategy(
                list(natural_cards), list(wild_cards),
                "O_flush_first", bomb_wilds, order
            )
            add_result(result, {
                "strategy": "O_flush_no_straight",
                "bomb_wilds": bomb_wilds,
                "order": list(order),
            })

    # 找最优（按 score 排序）
    if not all_results:
        singles = [CardGroup([c], "single", c.power) for c in hand_cards]
        return {
            "all_results": [],
            "best_index": 0,
            "bombs": [],
            "others": _groups_to_dict(singles),
            "zones": {"bombs": [], "notsort": _groups_to_dict(singles), "sortR": []},
        }

    # 按 score 排序（score tuple 越小越好）
    indexed = list(enumerate(all_results))
    indexed.sort(key=lambda x: x[1]["score"])
    sorted_results = [r for _, r in indexed]
    best_index_original = indexed[0][0]

    # best_index 指向排序后列表中的第一个
    return {
        "all_results": sorted_results,
        "best_index": 0,  # 排序后最优在索引0
        "bombs": sorted_results[0]["bombs"],
        "others": sorted_results[0]["others"],
        "zones": sorted_results[0]["zones"],
    }


def _result_stats(result: SortResult) -> dict:
    return {
        "single": len(result.singles),
        "pair": len(result.pairs),
        "triple": len(result.triples),
        "three_two": len(result.three_with_twos),
        "straight": len(result.straights),
        "board": len(result.boards),
        "steel": len(result.steels),
        "flush": len(result.flushes),
        "bomb": len(result.bombs),
        "king": len(result.kings),
    }


def _groups_to_dict(groups: list) -> list:
    """CardGroup 列表转为 JSON 可序列化的 dict 列表"""
    return [{
        "cards": [{
            "suit": c.suit,
            "rank": c.rank,
            "is_wild": c.is_wild,
            "cid": c.cid,
        } for c in g.cards],
        "group_type": g.group_type,
        "power": g.power,
        "size": g.size,
    } for g in groups]


# ================================================================
#  分区显示
# ================================================================

def partition_for_display(bombs: list, others: list) -> tuple:
    """
    三区划分：
      - 炸弹区: 王炸、同花顺、炸弹
      - 非理牌区: 三张、对子、单张 及 <5张的组
      - 理牌右区: 顺子(5)/木板(6)/钢板(6)/三带二(5)
    """
    sort_bombs = list(bombs)
    notsort = []
    sort_r = []

    for g in others:
        if g.group_type in ("straight", "board", "steel", "three_two") and g.size in (5, 6):
            sort_r.append(g)
        else:
            notsort.append(g)

    order_map = {"three_two": 0, "straight": 1, "board": 2, "steel": 3}
    sort_r.sort(key=lambda g: (order_map.get(g.group_type, 99), -g.power))

    return sort_bombs, notsort, sort_r


# ================================================================
#  公共接口（供 Web UI 等外部调用）
# ================================================================

def build_full_deck(level: str = "2") -> list:
    """构建 2 副牌共 108 张，标记级牌为癞子。返回 [(suit, rank, is_wild), ...]"""
    deck = []
    for _ in range(2):
        for suit in SUITS:
            for rank in RANKS:
                deck.append((suit, rank, rank == level))
    for _ in range(2):
        deck.append(("X", "SJ", False))
    for _ in range(2):
        deck.append(("X", "BJ", False))
    return deck


def deal_random_hand(level: str = "2", seed: int = None) -> list:
    """随机发一副 27 张手牌，返回 Card 对象列表"""
    if seed is not None:
        random.seed(int(seed))
    else:
        random.seed()

    deck = build_full_deck(level)
    random.shuffle(deck)
    hand = deck[:27]

    cards = []
    for i, (suit, rank, is_wild) in enumerate(hand):
        cards.append(Card(suit, rank, is_wild=is_wild, cid=i))
    return cards


def build_full_deck_cards(level: str = "2") -> list:
    """构建 2 副牌共 108 张，返回 Card 对象列表（每张牌有唯一 cid）。"""
    deck_specs = build_full_deck(level)
    cards = []
    for i, (suit, rank, is_wild) in enumerate(deck_specs):
        cards.append(Card(suit, rank, is_wild=is_wild, cid=i))
    return cards


def validate_deal(player_cards: list, total_cards: int = 108, players: int = 4,
                  hand_size: int = 27) -> dict:
    """
    校验自定义发牌是否合法。

    player_cards: [[card_cid, ...], ...]  每个玩家的 cid 列表
    返回 {"ok": bool, "error": str, "counts": [N,...]}
    """
    if len(player_cards) != players:
        return {"ok": False, "error": f"需要 {players} 个玩家，收到 {len(player_cards)} 个"}

    all_ids = set()
    for i, ids in enumerate(player_cards):
        for cid in ids:
            if cid in all_ids:
                return {"ok": False, "error": f"cid={cid} 重复出现（玩家 {i+1}）"}
            all_ids.add(cid)

    expected = set(range(total_cards))
    if all_ids != expected:
        missing = expected - all_ids
        extra = all_ids - expected
        msg = ""
        if missing:
            msg += f"缺少 cid: {sorted(missing)[:5]}..."
        if extra:
            msg += f" 无效 cid: {sorted(extra)[:5]}..."
        return {"ok": False, "error": msg.strip()}

    counts = [len(ids) for ids in player_cards]
    for i, c in enumerate(counts):
        if c != hand_size:
            return {"ok": False, "error": f"玩家 {i+1} 有 {c} 张牌，需要 {hand_size} 张",
                    "counts": counts}

    return {"ok": True, "error": "", "counts": counts}


def cards_to_json(cards: list) -> list:
    """Card 对象列表转为 JSON 可序列化的 dict 列表"""
    return [{
        "suit": c.suit,
        "rank": c.rank,
        "is_wild": c.is_wild,
        "cid": c.cid,
    } for c in cards]


# 花色 → 编码: ♦=1, ♣=2, ♥=3, ♠=4, ☆★=5
SUIT_CODE = {"D": 1, "C": 2, "H": 3, "S": 4, "SJ": 5, "BJ": 5}
# 牌值 → 编码: A=1, 2=2, ..., 9=9, 10=A(10), J=B(11), Q=C(12), K=D(13), 小王=E(14), 大王=F(15)
RANK_HEX = {
    "A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 0xA, "J": 0xB, "Q": 0xC, "K": 0xD, "SJ": 0xE, "BJ": 0xF
}


def card_to_hex(c) -> str:
    """单张牌 → 两位十六进制编码 (花色牌值)"""
    if c.rank in ("SJ", "BJ"):
        color = SUIT_CODE[c.rank]
    else:
        color = SUIT_CODE.get(c.suit, 0)
    value = RANK_HEX.get(c.rank, 0)
    if color == 0 or value == 0:
        return "00"
    return f"0x{color:X}{value:X}"


def cards_to_hex(cards: list) -> str:
    """手牌列表 → 逗号分隔的十六进制编码字符串"""
    return ",".join(card_to_hex(c) for c in cards)


# ================================================================
#  构建手牌（仅供测试用例使用）
# ================================================================

def build_hand(specs: list, wild_count: int = 0) -> list:
    """
    specs: [(suit, rank), ...]  自然牌
    wild_count: 癞子数量
    """
    cards = []
    cid = 0
    for suit, rank in specs:
        cards.append(Card(suit, rank, is_wild=False, cid=cid))
        cid += 1
    for _ in range(wild_count):
        cards.append(Card("H", "2", is_wild=True, cid=cid))
        cid += 1
    return cards


# ================================================================
#  打印
# ================================================================

TYPE_NAMES = {
    "king": "KingBomb", "flush": "FlushStr", "bomb": "Bomb",
    "straight": "Straight", "board": "Board", "steel": "Steel",
    "three_two": "3+2", "triple": "Triple", "pair": "Pair", "single": "Single",
}


def print_result(hand: list, bombs: list, others: list):
    """打印理牌结果"""
    n_wilds = sum(1 for c in hand if c.is_wild)
    n_nat = len(hand) - n_wilds
    print(f"\n{'='*65}")
    print(f"  Hand: {n_nat} natural + {n_wilds} wild = {len(hand)} cards")
    print(f"{'='*65}")

    sb, ns, sr = partition_for_display(bombs, others)

    def print_zone(name: str, groups: list):
        total = sum(g.size for g in groups)
        print(f"\n  [{name}] ({total} cards)")
        for g in groups:
            tag = TYPE_NAMES.get(g.group_type, g.group_type)
            if g.group_type == "bomb":
                tag = f"Bomb{g.size}"
            print(f"    [{tag:>10}] {' '.join(str(c) for c in g.cards)}")

    print_zone("Bomb Zone (sortBombs)", sb)
    print_zone("Mix Zone  (notsort) ", ns)
    print_zone("Right Zone (sortR)  ", sr)

    all_g = sb + ns + sr
    stats = Counter(g.group_type for g in all_g)
    print(f"\n  Stats: Single={stats.get('single',0)} Pair={stats.get('pair',0)} "
          f"Triple={stats.get('triple',0)} 3+2={stats.get('three_two',0)} "
          f"Str={stats.get('straight',0)} Board={stats.get('board',0)} "
          f"Steel={stats.get('steel',0)} Flush={stats.get('flush',0)} "
          f"Bomb={stats.get('bomb',0)} King={stats.get('king',0)}")
    print()


# ================================================================
#  测试用例（每个27张牌，含0~8癞子）
# ================================================================

def test_1():
    """Test 1: 4A=炸弹 + 3K+1癞=炸弹 + 3J + 对Q/对3 + 散牌 + 3癞"""
    print("\n" + "=" * 65)
    print("  Test 1: Bomb potential")
    cards = build_hand([
        # 4A = natural bomb
        ('S', 'A'), ('H', 'A'), ('C', 'A'), ('D', 'A'),
        # 3K + 1 wild = bomb
        ('S', 'K'), ('H', 'K'), ('C', 'K'),
        # 3J
        ('S', 'J'), ('H', 'J'), ('C', 'J'),
        # pairs
        ('S', 'Q'), ('H', 'Q'), 
        ('S', '3'), ('H', '3'),
        # singles
        ('S', '10'), ('H', '9'), ('C', '8'), ('D', '7'),
        ('S', '6'), ('H', '5'), ('C', '4'), ('D', '5'),
        ('S', '2'), ('C', '2'),
    ], wild_count=3)  # 24 natural + 3 wild = 27
    bombs, others = sort_8laizi(cards)
    print_result(cards, bombs, others)


def test_2():
    """Test 2: 同花顺潜力 + 4K炸弹 + 大小王"""
    print("\n" + "=" * 65)
    print("  Test 2: Flush straight potential")
    cards = build_hand([
        # S同花: A,3,4,5,7 (断2,6 -> 2癞补)
        ('S', 'A'), ('S', '3'), ('S', '4'), ('S', '5'), ('S', '7'),
        # 4K = bomb
        ('S', 'K'), ('H', 'K'), ('C', 'K'), ('D', 'K'),
        # H scattered
        ('H', 'A'), ('H', 'Q'), ('H', '10'), ('H', '9'), ('H', '8'),
        # others
        ('C', 'A'), ('C', 'Q'), ('C', 'J'), ('C', '10'),
        ('D', 'Q'), ('D', 'J'), ('D', '10'), ('D', '9'),
        # jokers
        ('X', 'BJ'), ('X', 'SJ'),
    ], wild_count=3)  # 24 + 3 = 27
    bombs, others = sort_8laizi(cards)
    print_result(cards, bombs, others)


def test_3():
    """Test 3: 8癞子满配"""
    print("\n" + "=" * 65)
    print("  Test 3: 8-wild full load")
    cards = build_hand([
        ('S', 'A'), ('S', 'A'), ('S', 'A'),
        ('S', 'K'), ('S', 'K'), ('S', 'K'),
        ('S', 'Q'), ('S', 'Q'),
        ('S', 'J'), ('S', '10'),
        ('H', 'A'), ('H', 'K'),
        ('H', 'Q'), ('H', 'J'),
        ('C', 'A'), ('C', 'K'), ('C', 'Q'),
        ('X', 'BJ'), ('X', 'SJ'),
    ], wild_count=8)  # 19 + 8 = 27
    bombs, others = sort_8laizi(cards)
    print_result(cards, bombs, others)


def test_4():
    """Test 4: 0癞子（纯自然牌）"""
    print("\n" + "=" * 65)
    print("  Test 4: 0 wild (pure natural)")
    cards = build_hand([
        # S straight: 10,J,Q,K,A
        ('S', 'A'), ('S', 'K'), ('S', 'Q'), ('S', 'J'), ('S', '10'),
        # H straight: 3,4,5,6,7
        ('H', '3'), ('H', '4'), ('H', '5'), ('H', '6'), ('H', '7'),
        # 4x4 = bomb
        ('C', '4'), ('C', '4'), ('C', '4'), ('C', '4'),
        # 3x8
        ('D', '8'), ('D', '8'), ('D', '8'),
        # pairs
        ('S', '9'), ('H', '9'),
        ('C', 'J'), ('D', 'J'),
        # singles
        ('S', '6'), ('H', 'Q'),
        ('C', '7'), ('D', '5'),
        # jokers
        ('X', 'BJ'), ('X', 'SJ'),
    ], wild_count=0)  # 27 + 0 = 27
    bombs, others = sort_8laizi(cards)
    print_result(cards, bombs, others)


def test_5():
    """Test 5: 三带二+钢板潜力 + 2癞"""
    print("\n" + "=" * 65)
    print("  Test 5: 3+2 + Steel plate potential")
    cards = build_hand([
        # 3x8, 3x9 -> steel (8,9)
        ('S', '8'), ('H', '8'), ('C', '8'),
        ('S', '9'), ('H', '9'), ('C', '9'),
        # 3x10 + pair J -> 3+2
        ('S', '10'), ('H', '10'), ('C', '10'),
        ('S', 'J'), ('H', 'J'),
        # 4xQ = bomb
        ('S', 'Q'), ('H', 'Q'), ('C', 'Q'), ('D', 'Q'),
        # pair 2
        ('C', '2'), ('D', '2'),
        # singles
        ('S', 'A'),
        ('S', '4'), ('H', '5'), ('C', '6'),
        ('D', '4'), ('D', '6'),
        # jokers
        ('X', 'BJ'), ('X', 'SJ'),
    ], wild_count=2)  # 25 natural + 2 wild = 27
    bombs, others = sort_8laizi(cards)
    print_result(cards, bombs, others)


def test_random():
    """随机手牌测试（模拟真实发牌）"""
    print("\n" + "=" * 65)
    print("  Test Random: Simulated deal (2 decks, level=2)")
    cards = deal_random_hand("2")
    wild_count = sum(1 for c in cards if c.is_wild)
    print(f"  Level=2, Wilds in hand: {wild_count}/8")

    bombs, others = sort_8laizi(cards)
    print_result(cards, bombs, others)


def main():
    random.seed(42)
    test_1()
    test_2()
    test_3()
    test_4()
    test_5()
    test_random()

    print(f"\n{'='*65}")
    print(f"  All tests completed.")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
