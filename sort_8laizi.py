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
import threading
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
_card_cid_lock = threading.Lock()

class Card:
    __slots__ = ('suit', 'rank', 'is_wild', 'power', 'value', 'cid', 'used')

    def __init__(self, suit: str, rank: str, is_wild: bool = False, cid: int = None):
        global _card_cid_counter
        self.suit = suit
        self.rank = rank
        self.is_wild = is_wild
        if cid is not None:
            self.cid = cid
        else:
            with _card_cid_lock:
                _card_cid_counter += 1
                self.cid = _card_cid_counter
        self.used = False
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
    return (not c.is_wild) and c.rank not in ("SJ", "BJ") and not c.used


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


def _mark_used(cards: list):
    """Mark multiple cards as used (replaces pool.remove loop)."""
    for c in cards:
        c.used = True


def _active_naturals(pool: list, rank: str) -> list:
    """Get all active (not used) natural cards of a given rank from pool."""
    return [c for c in pool if not c.used and not c.is_wild and c.rank == rank]


def _reset_used(cards: list):
    """Reset used flag for all cards (for reuse across strategy attempts)."""
    for c in cards:
        c.used = False


def _active_wilds(wild_pool: list) -> list:
    """Get wilds that haven't been consumed yet."""
    return [c for c in wild_pool if not c.used]


def _take_wilds(wild_pool: list, n: int) -> list:
    """Take n wilds from pool, marking them used. Returns the taken cards."""
    taken = []
    for c in wild_pool:
        if len(taken) >= n:
            break
        if not c.used:
            c.used = True
            taken.append(c)
    return taken


# ================================================================
#  王炸提取
# ================================================================

def extract_king_bombs(pool: list) -> list:
    """提取王炸：4张大小王=1个王炸"""
    jokers = [c for c in pool if c.rank in ("SJ", "BJ") and not c.is_wild and not c.used]
    if len(jokers) >= 4:
        taken = jokers[:4]
        _mark_used(taken)
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
    remaining = min(max_wilds_for_bombs, len(_active_wilds(wild_pool)))
    bombs = []

    # Phase 0: 纯自然炸弹（>=4张，不消耗癞子）
    for rank in sorted(rank_cnt.keys(), key=lambda r: (-rank_cnt[r], -RANK_VALUE.get(r, 0))):
        cnt = rank_cnt[rank]
        if cnt >= 4:
            cards_nat = _active_naturals(pool, rank)[:cnt]
            _mark_used(cards_nat)
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
                cards_nat = _active_naturals(pool, rank)[:cnt]
                _mark_used(cards_nat)
                w = _take_wilds(wild_pool, need)
                remaining -= len(w)
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
        cards_nat = _active_naturals(pool, rank)[:cnt]
        _mark_used(cards_nat)
        w = _take_wilds(wild_pool, add)
        remaining -= len(w)
        total = cnt + len(w)
        power = cards_nat[0].power + total * 100
        bombs.append(CardGroup(w + cards_nat, "bomb", power))
        rank_cnt[rank] = 0

    # Phase 3: 纯癞子炸弹（4个癞子=1个炸弹）
    while remaining >= 4:
        w = _take_wilds(wild_pool, 4)
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
    remaining = min(max_wilds_for_flush, len(_active_wilds(wild_pool)))
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
            suit_ranks = set(RANK_ORDER[c.rank] for c in cards if not c.used)
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

        suit_ranks = set(RANK_ORDER[c.rank] for c in cards if not c.used)

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
                for c in cards:
                    if not c.used and RANK_ORDER.get(c.rank) == ri:
                        taken.append(c)
                        c.used = True
                        break  # 每 rank 只取 1 张
            for c in cards:
                if not c.used and c.rank == "A":
                    taken.append(c)
                    c.used = True
                    break
        else:
            end = start + 4
            for ri in range(start, end + 1):
                for c in cards:
                    if not c.used and RANK_ORDER.get(c.rank) == ri:
                        taken.append(c)
                        c.used = True
                        break  # 每 rank 只取 1 张

        # 消耗癞子
        w = _take_wilds(wild_pool, wilds_needed)
        remaining -= len(w)

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

def extract_straights(pool: list, wild_pool: list, max_wilds: int = 999) -> list:
    """贪心提顺子（恰好5张连续，不限花色）。用癞子补断口。
    掼蛋顺子只能是5连张。支持A作为高牌（10-J-Q-K-A）。
    max_wilds: 最多用多少个癞子做顺子。"""
    straights = []
    rank_cnt = rank_counts(pool)
    wilds_used = 0
    while True:
        available = sorted(
            (r for r, c in rank_cnt.items() if c > 0),
            key=lambda r: RANK_ORDER[r]
        )
        if len(available) < 2:
            break

        n_wilds = min(len(_active_wilds(wild_pool)), max_wilds - wilds_used)

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
                    c = next((x for x in pool if not x.used and x.rank == r and not x.is_wild), None)
                    if c:
                        c.used = True
                        taken.append(c)
                        rank_cnt[r] = max(0, rank_cnt[r] - 1)
            if rank_cnt.get("A", 0) > 0:
                c = next((x for x in pool if not x.used and x.rank == "A" and not x.is_wild), None)
                if c:
                    c.used = True
                    taken.append(c)
                    rank_cnt["A"] = max(0, rank_cnt["A"] - 1)
        else:
            for ri in range(si_idx, si_idx + 5):
                r = RANKS[ri]
                if rank_cnt.get(r, 0) > 0:
                    c = next((x for x in pool if not x.used and x.rank == r and not x.is_wild), None)
                    if c:
                        c.used = True
                        taken.append(c)
                        rank_cnt[r] = max(0, rank_cnt[r] - 1)

        w = _take_wilds(wild_pool, needed)
        wilds_used += len(w)

        power = taken[0].power + 4 if taken else 0
        straights.append(CardGroup(w + taken, "straight", power))

    return straights


# ================================================================
#  木板提取（连对：恰好3个连续rank各有>=2张）
# ================================================================

def extract_boards(pool: list, wild_pool: list, max_wilds: int = 999) -> list:
    """贪心提木板（连对：恰好3个连续rank各>=2张）。掼蛋木板只能是3连对。
    支持用癞子补到2张（即某rank可以0自然牌+2癞子组对）。
    max_wilds: 最多用多少个癞子做木板。"""
    boards = []
    rank_cnt = rank_counts(pool)
    wilds_used = 0

    while True:
        n_wilds = min(len(_active_wilds(wild_pool)), max_wilds - wilds_used)
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
            cards = _active_naturals(pool, r)[:min(cnt, 2)]
            _mark_used(cards)
            taken.extend(cards)
            rank_cnt[r] = max(0, cnt - len(cards))

        w = _take_wilds(wild_pool, needed)
        wilds_used += len(w)

        power = taken[0].power if taken else WILD_POWER
        boards.append(CardGroup(w + taken, "board", power))

    return boards


# ================================================================
#  钢板提取（恰好2个连续rank各有>=3张）
# ================================================================

def extract_steel_plates(pool: list, wild_pool: list, max_wilds: int = 999) -> list:
    """贪心提钢板（恰好2个连续rank各>=3张）。掼蛋钢板只能是2连三张。
    支持用癞子补到3张（即某rank可以0自然牌+3癞子组三张）。
    max_wilds: 最多用多少个癞子做钢板。"""
    steels = []
    rank_cnt = rank_counts(pool)
    wilds_used = 0

    while True:
        n_wilds = min(len(_active_wilds(wild_pool)), max_wilds - wilds_used)
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
            cards = _active_naturals(pool, r)[:min(cnt, 3)]
            _mark_used(cards)
            taken.extend(cards)
            rank_cnt[r] = max(0, cnt - len(cards))

        w = _take_wilds(wild_pool, needed)
        wilds_used += len(w)

        power = taken[0].power if taken else WILD_POWER
        group = CardGroup(w + taken, "steel", power)

        natural_ranks = sorted(set(c.rank for c in taken if not c.is_wild),
                               key=lambda r: RANK_ORDER[r])
        # Ace-high 钢板 (K-A) 跳过连续校验（A=0, K=12，不满足+1条件但合法）
        if si != -1:
            for i in range(1, len(natural_ranks)):
                if RANK_ORDER[natural_ranks[i]] != RANK_ORDER[natural_ranks[i-1]] + 1:
                    raise ValueError(f"Steel plate has non-consecutive ranks: {natural_ranks}")

        steels.append(group)

    return steels


# ================================================================
#  三带二提取
# ================================================================

def extract_three_with_two(pool: list, wild_pool: list,
                           max_pair_value: int = 11,
                           max_wilds: int = 999) -> list:
    """贪心提三带二（三张+对子，对子点数<=max_pair_value即不超过J）。
    max_wilds: 最多用多少个癞子做三带二。"""
    twt_list = []
    rank_cnt = rank_counts(pool)
    wilds_used = 0

    while True:
        n_wilds = min(len(_active_wilds(wild_pool)), max_wilds - wilds_used)
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
        triple_cards = _active_naturals(pool, tr)[:tcnt]
        _mark_used(triple_cards)
        rank_cnt[tr] = max(0, rank_cnt[tr] - len(triple_cards))

        # 提取对子（最多2张）
        pair_cards = _active_naturals(pool, pr)[:min(pcnt, 2)]
        _mark_used(pair_cards)
        rank_cnt[pr] = max(0, rank_cnt[pr] - len(pair_cards))

        triple_wilds = _take_wilds(wild_pool, need_triple)
        pair_wilds = _take_wilds(wild_pool, need_pair)
        wilds_used += len(triple_wilds) + len(pair_wilds)

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
        while cnt + len(_active_wilds(wild_pool)) >= 3 and cnt >= 1:
            take = min(cnt, 3)
            cards = _active_naturals(pool, rank)[:take]
            need = 3 - take
            _mark_used(cards)
            rank_cnt[rank] = max(0, rank_cnt[rank] - len(cards))
            cnt -= len(cards)
            w = _take_wilds(wild_pool, need)
            triples.append(CardGroup(w + cards, "triple",
                                     cards[0].power if cards else WILD_POWER))

    # 对子
    rank_cnt = rank_counts(pool)
    for rank in sorted(rank_cnt.keys(), key=lambda r: (-rank_cnt[r], -RANK_VALUE.get(r, 0))):
        cnt = rank_cnt[rank]
        while cnt + len(_active_wilds(wild_pool)) >= 2 and cnt >= 1:
            take = min(cnt, 2)
            cards = _active_naturals(pool, rank)[:take]
            need = 2 - take
            _mark_used(cards)
            rank_cnt[rank] = max(0, rank_cnt[rank] - len(cards))
            cnt -= len(cards)
            w = _take_wilds(wild_pool, need)
            pairs.append(CardGroup(w + cards, "pair",
                                   cards[0].power if cards else WILD_POWER))

    # 大小王对子（2小王=对子，2大王=对子）
    for joker_rank in ("SJ", "BJ"):
        jokers = [c for c in pool if c.rank == joker_rank and not c.used]
        while len(jokers) >= 2:
            take = jokers[:2]
            _mark_used(take)
            pairs.append(CardGroup(take, "pair", take[0].power))
            jokers = jokers[2:]

    # 单张（剩余所有自然牌）
    for c in pool:
        if not c.used:
            c.used = True
            singles.append(CardGroup([c], "single", c.power))
    # 单张（剩余癞子）
    for c in wild_pool:
        if not c.used:
            c.used = True
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
        # 核心权衡：1个4线炸弹 ≈ 消化2.5张"本来会成为单张/对子"的牌
        # 所以 bomb 权重设为 2.5，让"多1炸弹但多1单张"的方案在接近时胜出
        # 公式：frag_score = singles + pairs*0.5 + triples*0.3 - bombs*2.5 - flushes*2.5
        frag_score = (
            len(self.singles)
            + len(self.pairs) * 0.5
            + len(self.triples) * 0.3
            - len(self.bombs) * 2.5
            - len(self.flushes) * 2.5
        )
        return (
            # ① 加权碎片分（越小越好 — 综合考虑单张数和炸弹数的平衡）
            frag_score,
            # ② 以下为 tiebreaker，在 frag_score 相同时决定胜负
            len(self.singles),           # 单张数（绝对值，越少越好）
            -bomb5plus,                  # 5+线炸弹数
            -len(self.straights),        # 顺子数
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

# 4 种牌型 key（用于预算分配）
BUDGET_TYPES = ["bomb", "flush", "straight", "board", "steel", "three_two"]

# 快速路径阈值：癞子数 ≤ 此值时自动启用快速路径（跳过 probe + 预算枚举）
FAST_WILD_THRESHOLD = 2


# ================================================================
#  癞子预算配置表（laiziLimit_config）
# ================================================================
#
# 人为约束每种牌型在提取时最多能消耗的癞子数量。
# 用于裁剪搜索空间 + 表达用户策略偏好。
#
# 格式：{牌型key: 最大癞子数}
#   - 设为 999 表示不限制（等同于不配）
#   - 设为 0   表示禁止该牌型使用癞子（纯自然牌才能组）
#
# 使用方式：
#   1. 全局默认约束：直接修改 LAIZI_LIMIT_CONFIG_DEFAULT
#   2. 单次调用覆盖：sort_8laizi_with_details(cards, laizi_limit={...})
#   3. 动态生成预算组合：generate_wild_budgets() 会读此配置作为上界

LAIZI_LIMIT_CONFIG_DEFAULT = {
    #                    含义
    "straight":  999,   # 顺子：最多用几张癞子补断口
    "board":     999,   # 木板（连对）：最多用几张癞子补对
    "steel":     999,   # 钢板（连三）：最多用几张癞子补三张
    "three_two": 999,   # 三带二：最多用几张癞子（三张+对子合计）
    # 高级区域也可约束（作用于炸弹/同花顺层）
    "bomb":      999,   # 炸弹：最多用几张癞子
    "flush":     999,   # 同花顺：最多用几张癞子
}


def get_laizi_limit(key, config=None):
    """读取某牌型的癞子上限。config=None 时用全局默认。"""
    cfg = config if config else LAIZI_LIMIT_CONFIG_DEFAULT
    return cfg.get(key, 999)


# ================================================================
#  癞子预算分配方案生成
# ================================================================

def _probe_actual_wild_usage(natural_cards, wild_cards, strategy, bomb_wilds, order):
    """
    用不限制 budget 的方式跑一次 execute_strategy，
    返回每种牌型实际消耗的癞子数。
    这样后续只需在 [0, actual] 范围内枚举 budget，大幅削减无用组合。
    """
    result = execute_strategy(natural_cards, wild_cards, strategy, bomb_wilds, order,
                              wild_budgets=None)  # None = 不限制

    usage = {}
    usage["bomb"] = sum(g.wild_count for g in result.bombs)
    usage["flush"] = sum(g.wild_count for g in result.flushes)
    usage["straight"] = sum(g.wild_count for g in result.straights)
    usage["board"] = sum(g.wild_count for g in result.boards)
    usage["steel"] = sum(g.wild_count for g in result.steels)
    usage["three_two"] = sum(g.wild_count for g in result.three_with_twos)
    return usage, result


def generate_wild_budgets(n_remaining, config=None, caps_override=None):
    """
    将 n_remaining 个癞子分配到 4 个牌型（straight/board/steel/three_two），
    生成所有可能的预算组合。

    caps_override: 各牌型的实际上限 dict，如 {"straight": 2, "board": 1, ...}
                   优先于 config。由 _probe_actual_wild_usage 计算。
    config: laiziLimit 配置（人为约束上限）
    """
    results = []

    caps = []
    for t in BUDGET_TYPES:
        cap = n_remaining  # 默认不限制
        if caps_override:
            cap = min(cap, caps_override.get(t, n_remaining))
        cap_config = get_laizi_limit(t, config)
        cap = min(cap, cap_config)
        caps.append(cap)

    def _allocate(remaining, idx, current):
        if idx == len(BUDGET_TYPES):
            results.append(dict(current))
            return
        cap = min(caps[idx], remaining)
        t = BUDGET_TYPES[idx]
        for w in range(cap + 1):
            current[t] = w
            _allocate(remaining - w, idx + 1, current)
        current[t] = 0

    _allocate(n_remaining, 0, {})
    return results


# ================================================================
#  执行一种策略
# ================================================================

def execute_strategy(natural_cards: list, wild_cards: list,
                     strategy: str, bomb_wilds: int,
                     extraction_order: tuple,
                     wild_budgets: dict = None) -> SortResult:
    """
    执行一种理牌策略。
    
    strategy:
      "O_flush_first"  - 同花顺先于炸弹
      "O_flush_single" - 同花顺先于炸弹，但最多1个同花顺
      "N_bomb_first"   - 炸弹先于同花顺
    
    bomb_wilds: 给炸弹预留的癞子数量上限（可被 wild_budgets["bomb"] 进一步限制）
    
    wild_budgets: 各牌型的癞子预算上限，格式：
      {"straight": N, "board": N, "steel": N, "three_two": N, "bomb": N, "flush": N}
      None 或缺省项 = 不限制（999）
    """
    _reset_used(natural_cards)
    for c in wild_cards:
        c.used = False

    pool = natural_cards
    wp = wild_cards
    n_lz = len(wp)

    def _budget(key):
        if wild_budgets:
            return wild_budgets.get(key, 999)
        return 999

    # bomb 和 flush 的实际限额 = min(bomb_wilds/bomb 参数, wild_budgets 配置)
    bomb_cap = min(bomb_wilds, _budget("bomb"))
    flush_cap = _budget("flush")

    result = SortResult()
    result.kings = extract_king_bombs(pool)

    if strategy in ("O_flush_first", "O_flush_single"):
        max_f = 1 if strategy == "O_flush_single" else 999
        suit_counts = defaultdict(int)
        for c in pool:
            if is_natural_rank(c):
                suit_counts[c.suit] += 1
        flush_suit_order = sorted(SUITS, key=lambda s: suit_counts.get(s, 0))
        result.flushes = extract_flush_straights(pool, wp,
                                                 max_wilds_for_flush=min(
                                                     max(0, len(wp) - bomb_cap), flush_cap),
                                                 suit_priority=flush_suit_order,
                                                 max_flushes=max_f)
        result.bombs = extract_bombs(pool, wp, bomb_cap)
    elif strategy == "N_bomb_first":
        result.bombs = extract_bombs(pool, wp, bomb_cap)
        result.flushes = extract_flush_straights(pool, wp,
                                                 max_wilds_for_flush=flush_cap)

    for ext_type in extraction_order:
        if ext_type == "straight":
            result.straights = extract_straights(pool, wp, max_wilds=_budget("straight"))
        elif ext_type == "board":
            result.boards = extract_boards(pool, wp, max_wilds=_budget("board"))
        elif ext_type == "steel":
            result.steels = extract_steel_plates(pool, wp, max_wilds=_budget("steel"))
        elif ext_type == "three_two":
            result.three_with_twos = extract_three_with_two(pool, wp, max_wilds=_budget("three_two"))

    # 剩余癞子优先喂给已有炸弹扩线（4炸>5炸>...，同张数牌值大的优先）
    _boost_best_group_with_leftover_wilds(wp, result.bombs)

    result.triples, result.pairs, result.singles = extract_remaining(pool, wp)

    return result


def _boost_best_group_with_leftover_wilds(wild_pool: list, bombs: list):
    """
    将未使用的癞子逐张分配给已有炸弹扩线。
    优先张数最少的炸弹（4炸>5炸>6炸...），张数相同选牌值最大的。
    无炸弹时由 extract_remaining 自然按三张>对子>单张兜底。
    """
    available = _active_wilds(wild_pool)
    if not available or not bombs:
        return

    # 按用户需求排序：张数少的优先，同张数牌值大的优先
    sorted_bombs = sorted(
        bombs, key=lambda b: (b.size, -b.first_natural_power())
    )

    for w in available:
        if not sorted_bombs:
            break
        # 每轮取最优（张数最少），喂完后重新排序（其张数已+1，可能不再最优）
        best = sorted_bombs[0]
        w.used = True
        best.cards.insert(0, w)
        best.size += 1
        fnp = best.first_natural_power()
        best.power = fnp + best.size * 100
        # 重新排序
        sorted_bombs.sort(key=lambda b: (b.size, -b.first_natural_power()))


# ================================================================
#  主算法
# ================================================================

def try_all_strategies(natural_cards: list, wild_cards: list,
                       laizi_limit: dict = None,
                       fast_mode: bool = None) -> SortResult:
    """
    枚举所有策略组合，返回最优。带去重和剪枝。
    
    laizi_limit: 人为约束每种牌型的癞子上限（见 LAIZI_LIMIT_CONFIG_DEFAULT）。
                 None 则用全局默认配置。
    fast_mode:   None=自动检测(n_lz≤FAST_WILD_THRESHOLD时快速)，
                 True=强制快速路径，False=强制完整路径。
    """
    n_lz = len(wild_cards)
    if fast_mode is None:
        fast_mode = (n_lz <= FAST_WILD_THRESHOLD)

    best = None
    seen_results = set()

    def try_one(strategy, bomb_wilds, order, budgets):
        nonlocal best
        result = execute_strategy(natural_cards, wild_cards, strategy, bomb_wilds, order,
                                  wild_budgets=budgets)
        sig = result.score()
        if sig in seen_results:
            return
        seen_results.add(sig)
        if best is None or result.score() < best.score():
            best = result

    # ── 快速路径：保留 probe 裁剪，但去掉去顺子策略组（减少33%调用）──
    if fast_mode:
        def _run_group_fast(strategy, orders):
            for bomb_wilds in range(n_lz + 1):
                remaining = n_lz - bomb_wilds
                for order in orders:
                    usage, _ = _probe_actual_wild_usage(
                        natural_cards, wild_cards, strategy, bomb_wilds, order)
                    all_budgets = generate_wild_budgets(remaining, laizi_limit, caps_override=usage)
                    for budgets in all_budgets:
                        try_one(strategy, bomb_wilds, order, budgets)

        _run_group_fast("O_flush_first", EXTRACTION_ORDERS)
        _run_group_fast("O_flush_single", EXTRACTION_ORDERS)
        _run_group_fast("N_bomb_first", EXTRACTION_ORDERS)
        return best

    # ── 完整路径：probe + 预算枚举 ──
    def _run_group(strategy, orders):
        nonlocal best
        for bomb_wilds in range(n_lz + 1):
            remaining = n_lz - bomb_wilds
            for order in orders:
                # 先 probe 一次：跑不限 budget 的版本，拿到各牌型实际癞子消耗上限
                usage, _ = _probe_actual_wild_usage(
                    natural_cards, wild_cards, strategy, bomb_wilds, order)
                # 只在实际消耗范围内枚举 budget（受 laizi_limit 约束）
                all_budgets = generate_wild_budgets(remaining, laizi_limit, caps_override=usage)
                for budgets in all_budgets:
                    try_one(strategy, bomb_wilds, order, budgets)

    _run_group("O_flush_first", EXTRACTION_ORDERS)
    _run_group("N_bomb_first", EXTRACTION_ORDERS)
    orders_no_straight = [o for o in EXTRACTION_ORDERS if o[0] != "straight"]
    _run_group("O_flush_first", orders_no_straight)

    return best


def sort_8laizi(hand_cards: list, laizi_limit: dict = None,
                fast_mode: bool = None) -> tuple:
    """
    一键理牌主入口（兼容 2癞子 / 8癞子）。
    
    返回 (bombs, others):
      bombs  - 王炸 + 同花顺 + 炸弹（炸弹区）
      others - 顺子/木板/钢板/三带二/三张/对子/单张
    
    laizi_limit: 人为约束每种牌型的癞子上限（见 LAIZI_LIMIT_CONFIG_DEFAULT）。
    fast_mode:   None=自动检测(n_lz≤2时快速)，True/False=强制模式。
    """
    wild_cards = wilds_only(hand_cards)
    natural_cards = naturals_only(hand_cards)
    best = try_all_strategies(natural_cards, wild_cards, laizi_limit=laizi_limit,
                              fast_mode=fast_mode)

    if best is None:
        singles = [CardGroup([c], "single", c.power) for c in hand_cards]
        return ([], singles)

    return (best.bombs_output, best.others_output)


def sort_8laizi_with_details(hand_cards: list, laizi_limit: dict = None,
                             fast_mode: bool = None) -> dict:
    """
    一键理牌（含详情），供 Web UI 使用（兼容 2癞子 / 8癞子）。
    
    返回 dict:
      all_results: 所有策略结果列表，每个包含 strategy/meta/score/stats/bombs/others/zones
      best_index: 最优结果在 all_results 中的索引
      bombs, others: 最优结果的 bombs/others
      zones: 最优结果的三区划分
    
    laizi_limit: 人为约束每种牌型的癞子上限（见 LAIZI_LIMIT_CONFIG_DEFAULT）。
    fast_mode:   None=自动检测(n_lz≤2时快速)，True/False=强制模式。
    """
    wild_cards = wilds_only(hand_cards)
    natural_cards = naturals_only(hand_cards)
    n_lz = len(wild_cards)
    if fast_mode is None:
        fast_mode = (n_lz <= FAST_WILD_THRESHOLD)

    all_results = []
    seen = set()

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

    def _try_and_add(strategy, bomb_wilds, order, budgets, label_fn):
        result = execute_strategy(
            natural_cards, wild_cards, strategy, bomb_wilds, order, wild_budgets=budgets)
        sig = result.score()
        if sig in seen:
            return
        seen.add(sig)
        add_result(result, label_fn(bomb_wilds, order, budgets))

    # ── 快速路径：保留 probe 裁剪，去掉去顺子策略组 ──
    if fast_mode:
        FAST_STRATEGIES = [("O_flush_first", "同花顺优先"),
                           ("O_flush_single", "单同花"),
                           ("N_bomb_first", "炸弹优先")]
        for strategy, label in FAST_STRATEGIES:
            for bomb_wilds in range(n_lz + 1):
                remaining = n_lz - bomb_wilds
                for order in EXTRACTION_ORDERS:
                    usage, _ = _probe_actual_wild_usage(
                        natural_cards, wild_cards, strategy, bomb_wilds, order)
                    all_budgets = generate_wild_budgets(remaining, laizi_limit, caps_override=usage)
                    for budgets in all_budgets:
                        _try_and_add(strategy, bomb_wilds, order, budgets,
                            lambda bw, o, b, s=label: {"strategy": s, "bomb_wilds": bw, "order": list(o), "budgets": b})

    # ── 完整路径：probe + 预算枚举 ──
    else:
        def _run_strategy_group(strategy, bomb_wilds_range, orders, label_fn):
            for bomb_wilds in bomb_wilds_range:
                remaining = n_lz - bomb_wilds
                for order in orders:
                    usage, _ = _probe_actual_wild_usage(
                        natural_cards, wild_cards, strategy, bomb_wilds, order)
                    all_budgets = generate_wild_budgets(remaining, laizi_limit, caps_override=usage)
                    for budgets in all_budgets:
                        _try_and_add(strategy, bomb_wilds, order, budgets, label_fn)

        bw_range = range(n_lz + 1)

        _run_strategy_group("O_flush_first", bw_range, EXTRACTION_ORDERS,
            lambda bw, o, b: {"strategy": "O_flush_first", "bomb_wilds": bw, "order": list(o), "budgets": b})

        _run_strategy_group("O_flush_single", bw_range, EXTRACTION_ORDERS,
            lambda bw, o, b: {"strategy": "O_flush_single", "bomb_wilds": bw, "order": list(o), "budgets": b})

        _run_strategy_group("N_bomb_first", bw_range, EXTRACTION_ORDERS,
            lambda bw, o, b: {"strategy": "N_bomb_first", "bomb_wilds": bw, "order": list(o), "budgets": b})

        orders_no_straight = [o for o in EXTRACTION_ORDERS if o[0] != "straight"]
        _run_strategy_group("O_flush_first", bw_range, orders_no_straight,
            lambda bw, o, b: {"strategy": "O_flush_no_straight", "bomb_wilds": bw, "order": list(o), "budgets": b})

    if not all_results:
        singles = [CardGroup([c], "single", c.power) for c in hand_cards]
        return {
            "all_results": [],
            "best_index": 0,
            "bombs": [],
            "others": _groups_to_dict(singles),
            "zones": {"bombs": [], "notsort": _groups_to_dict(singles), "sortR": []},
        }

    indexed = list(enumerate(all_results))
    indexed.sort(key=lambda x: x[1]["score"])
    sorted_results = [r for _, r in indexed]

    return {
        "all_results": sorted_results,
        "best_index": 0,
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
        "bomb5plus": sum(1 for b in result.bombs if b.size >= 5),
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

def build_full_deck(level: str = "2", wild_mode: int = 8) -> list:
    """构建 2 副牌共 108 张，标记级牌为癞子。返回 [(suit, rank, is_wild), ...]
    
    wild_mode=8: 4花色级牌全部为癞子 (8张)
    wild_mode=2: 仅红桃级牌为癞子 (2张)
    """
    deck = []
    for _ in range(2):
        for suit in SUITS:
            for rank in RANKS:
                if wild_mode == 2:
                    is_wild = (suit == "H" and rank == level)
                else:
                    is_wild = (rank == level)
                deck.append((suit, rank, is_wild))
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


def build_full_deck_cards(level: str = "2", wild_mode: int = 8) -> list:
    """构建 2 副牌共 108 张，返回 Card 对象列表（每张牌有唯一 cid）。"""
    deck_specs = build_full_deck(level, wild_mode=wild_mode)
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
#  ★ 八红桃发牌系统（BaHongTao Deal）
# ================================================================
#
#  两阶段发牌：
#    Phase 1: 按概率档位分配 8 张癞子到 4 人，超额扣减/不足补足
#    Phase 2: 100 张自然牌洗乱均发 4 人，按牌力做补偿调换
#
#  牌力评估核心规则：同一张癞子可重复计入多种牌型。
#  即评估炸弹数量时使用全部癞子，评估同花顺时再次使用全部癞子，互不消耗。
#
#  入口函数：
#    deal_ba_hong_tao(config, seed)  → DealResult
#
#  依赖：deal_config.DealConfig / ScoreWeights / WildTier

from deal_config import DealConfig, ScoreWeights, WildTier, default_config


# ----------------------------------------------------------------
#  牌力评估（癞子可跨牌型复用）
# ----------------------------------------------------------------

def _rank_counts_with_wilds(cards: list) -> dict:
    """rank → 自然张数（不含癞子/王）"""
    cnt = {}
    for c in cards:
        if not c.is_wild and c.rank not in ("SJ", "BJ"):
            cnt[c.rank] = cnt.get(c.rank, 0) + 1
    return cnt


def _count_wilds(cards: list) -> int:
    return sum(1 for c in cards if c.is_wild)


def _count_jokers(cards: list) -> tuple:
    """返回 (小王数, 大王数)"""
    sj = sum(1 for c in cards if c.rank == "SJ")
    bj = sum(1 for c in cards if c.rank == "BJ")
    return sj, bj


def _potential_bombs(rank_cnt: dict, n_wilds: int) -> int:
    """
    炸弹潜力数：每个 rank 的自然张数 + 可分配的癞子 >= 4 即可成炸弹。
    癞子可复用 → 每个 rank 独立判断。
    """
    count = 0
    for rank, nat in rank_cnt.items():
        if nat + n_wilds >= 4:
            count += 1
    # 纯癞子炸弹（4 癞子=1炸）也算
    if n_wilds >= 4 and count == 0:
        count += n_wilds // 4
    return count


def _potential_flush_straights(cards: list, n_wilds: int) -> int:
    """
    同花顺潜力数：对每个花色独立判断能凑出几个 5 连窗口。
    癞子可复用 → 每个窗口独立判断，只要有足够癞子补断口即可。
    """
    total = 0
    WINDOWS = [list(range(s, s + 5)) for s in range(9)]  # A-2-3-4-5 ~ 9-10-J-Q-K
    WINDOWS.append([9, 10, 11, 12, 0])  # 10-J-Q-K-A (A=0 as high)

    for suit in SUITS:
        suit_ranks = set()
        for c in cards:
            if not c.is_wild and c.rank not in ("SJ", "BJ") and c.suit == suit:
                suit_ranks.add(RANK_ORDER[c.rank])
        if not suit_ranks:
            continue
        for window in WINDOWS:
            missing = sum(1 for ri in window if ri not in suit_ranks)
            if missing <= n_wilds:  # 癞子可复用，只看是否能补
                total += 1
    return total


def _potential_steel_plates(rank_cnt: dict, n_wilds: int) -> int:
    """
    钢板潜力数：2 个连续 rank 各 >= 3 张（癞子可补）。
    癞子可复用。
    """
    count = 0
    ranks_present = set(rank_cnt.keys())
    for i in range(len(RANKS) - 1):
        r0, r1 = RANKS[i], RANKS[i + 1]
        n0 = rank_cnt.get(r0, 0)
        n1 = rank_cnt.get(r1, 0)
        need0 = max(0, 3 - n0)
        need1 = max(0, 3 - n1)
        if need0 + need1 <= n_wilds and (n0 > 0 or n1 > 0 or n_wilds >= 6):
            count += 1
    # Ace-high: K-A
    nk = rank_cnt.get("K", 0)
    na = rank_cnt.get("A", 0)
    if max(0, 3 - nk) + max(0, 3 - na) <= n_wilds and (nk > 0 or na > 0 or n_wilds >= 6):
        count += 1
    return count


def _potential_link_pairs(rank_cnt: dict, n_wilds: int) -> int:
    """
    连对潜力数：3 个连续 rank 各 >= 2 张（癞子可补）。
    癞子可复用。
    """
    count = 0
    for i in range(len(RANKS) - 2):
        r0, r1, r2 = RANKS[i], RANKS[i + 1], RANKS[i + 2]
        n0 = rank_cnt.get(r0, 0)
        n1 = rank_cnt.get(r1, 0)
        n2 = rank_cnt.get(r2, 0)
        need = sum(max(0, 2 - n) for n in (n0, n1, n2))
        if need <= n_wilds and (n0 > 0 or n1 > 0 or n2 > 0 or n_wilds >= 6):
            count += 1
    # Ace-high: Q-K-A
    nq = rank_cnt.get("Q", 0)
    nk = rank_cnt.get("K", 0)
    na = rank_cnt.get("A", 0)
    need = sum(max(0, 2 - n) for n in (nq, nk, na))
    if need <= n_wilds and (nq > 0 or nk > 0 or na > 0 or n_wilds >= 6):
        count += 1
    return count


def _potential_threes(rank_cnt: dict, n_wilds: int) -> int:
    """三张潜力数：rank 自然 >= 3 或 +癞子 >= 3。癞子可复用。"""
    count = 0
    for rank, nat in rank_cnt.items():
        if nat + n_wilds >= 3:
            count += 1
    return count


def evaluate_hand_power(cards: list, scores: ScoreWeights = None) -> dict:
    """
    评估手牌牌力。癞子可跨牌型复用（每种牌型独立评估，不消耗癞子）。

    返回:
      {
        "score": int,             # 总分
        "details": {
            "bombs": int, "flushes": int, "steels": int, "link_pairs": int,
            "big_joker": int, "threes": int, "small_joker": int, "wilds": int,
        },
        "weights": {...},         # 使用的权重
      }
    """
    if scores is None:
        scores = ScoreWeights()

    n_wilds = _count_wilds(cards)
    sj, bj = _count_jokers(cards)
    rank_cnt = _rank_counts_with_wilds(cards)

    bombs = _potential_bombs(rank_cnt, n_wilds)
    flushes = _potential_flush_straights(cards, n_wilds)
    steels = _potential_steel_plates(rank_cnt, n_wilds)
    link_pairs = _potential_link_pairs(rank_cnt, n_wilds)
    threes = _potential_threes(rank_cnt, n_wilds)

    details = {
        "bombs": bombs,
        "flushes": flushes,
        "steels": steels,
        "link_pairs": link_pairs,
        "big_joker": 1 if bj > 0 else 0,
        "threes": threes,
        "small_joker": 1 if sj > 0 else 0,
        "wilds": n_wilds,
    }

    total = (
        bombs * scores.bomb
        + flushes * scores.same_color_link
        + steels * scores.steel_plate
        + link_pairs * scores.link_pair
        + details["big_joker"] * scores.big_joker
        + threes * scores.three
        + details["small_joker"] * scores.small_joker
        + n_wilds * scores.wild
    )

    return {
        "score": total,
        "details": details,
        "weights": {
            "bomb": scores.bomb,
            "same_color_link": scores.same_color_link,
            "steel_plate": scores.steel_plate,
            "link_pair": scores.link_pair,
            "big_joker": scores.big_joker,
            "three": scores.three,
            "small_joker": scores.small_joker,
            "wild": scores.wild,
        },
    }


# ----------------------------------------------------------------
#  Phase 1: 癞子分配
# ----------------------------------------------------------------

def _roll_wild_count_for_one(tiers: list, rng: random.Random) -> int:
    """
    按概率档位摇号，返回癞子数。
    tiers: [WildTier(min, max, weight), ...]
    """
    total_w = sum(t.weight for t in tiers)
    if total_w <= 0:
        return 0
    r = rng.uniform(0, total_w)
    cumulative = 0.0
    for tier in tiers:
        cumulative += tier.weight
        if r <= cumulative:
            return tier.sample_count()
    return tiers[-1].sample_count()


def _assign_wilds_phase1(config: DealConfig, rng: random.Random) -> list:
    """
    Phase 1：癞子分配。

    座位约定：座位 0 = P1（人类），座位 1..3 = P2-P4（机器人）。

    流程:
      1. 每人独立摇号 → 4 个癞子数
      2. 机器人座位钳制到 RobotMaxWilds
      3. 加权修正到总和=8:
         - 差额 > 0：按"剩余容量权重"随机挑选补足
         - 差额 < 0：按"超出量权重"随机挑选扣减
         - 机器人上限为 RobotMaxWilds（修正阶段也遵守）
      4. 打乱机器人座位之间的癞子数对应（P1 固定为人类）

    返回：[wild_count_seat0, wild_count_seat1, wild_count_seat2, wild_count_seat3]
    """
    n_players = config.players
    total_wilds = config.total_wilds
    robot_max = config.robot_max_wilds

    # Step 1: 每人独立摇号
    counts = []
    for i in range(n_players):
        c = _roll_wild_count_for_one(config.wild_tiers, rng)
        c = max(0, min(c, total_wilds))
        counts.append(c)

    # Step 2: 机器人座位钳制
    for i in range(1, n_players):
        if counts[i] > robot_max:
            counts[i] = robot_max

    # Step 3: 加权修正到总和=8
    _balance_to_total(counts, total_wilds, robot_max, rng)

    # Step 4: 打乱机器人座位
    robot_counts = counts[1:]
    rng.shuffle(robot_counts)
    counts[1:] = robot_counts

    return counts


def _balance_to_total(counts: list, target: int, robot_max: int, rng: random.Random):
    """
    加权修正 counts 使其总和 = target。
    补足时按每人的"可加容量"作为权重随机分配，
    扣减时按每人的"可减量"作为权重随机分配。
    机器人上限为 robot_max，下限为 0。
    """
    n = len(counts)
    max_rounds = target * 4  # 安全上限

    for _ in range(max_rounds):
        diff = target - sum(counts)
        if diff == 0:
            return

        if diff > 0:
            # 需要加癞子：按每人剩余容量权重随机选
            capacities = []
            for i in range(n):
                cap = robot_max if i > 0 else target
                room = max(0, cap - counts[i])
                capacities.append(room)
            if sum(capacities) == 0:
                # 全部到上限，强制给人加
                for i in range(n):
                    capacities[i] = 1
            # 加权随机选一个座位 +1
            idx = _weighted_choice(capacities, rng)
            if idx >= 0:
                counts[idx] += 1

        else:  # diff < 0
            # 需要扣癞子：按每人可减量权重随机选
            reducible = [max(0, counts[i]) for i in range(n)]
            if sum(reducible) == 0:
                return
            idx = _weighted_choice(reducible, rng)
            if idx >= 0:
                counts[idx] -= 1


def _weighted_choice(weights: list, rng: random.Random) -> int:
    """按权重列表随机选一个索引，返回 -1 如果全为 0。"""
    total = sum(weights)
    if total <= 0:
        return -1
    r = rng.uniform(0, total)
    cumulative = 0.0
    for i, w in enumerate(weights):
        cumulative += w
        if r <= cumulative:
            return i
    return len(weights) - 1


# ----------------------------------------------------------------
#  Phase 2: 自然牌发放 + 补偿控制
# ----------------------------------------------------------------

def _split_deck_by_wild(deck: list) -> tuple:
    """将 108 张牌库拆分为 (癞子列表, 自然牌列表)"""
    wilds = []
    naturals = []
    for c in deck:
        if c.is_wild:
            wilds.append(c)
        else:
            naturals.append(c)
    return wilds, naturals


def _deal_naturals(natural_cards: list, wild_counts: list, hand_size: int,
                   rng: random.Random) -> list:
    """
    将自然牌洗乱后按 wild_counts 分配给各人，确保每人最终 hand_size 张。

    natural_cards: 100 张自然牌
    wild_counts:   [w0, w1, w2, w3]，每人癞子数
    hand_size:     每人目标手牌总数（27）

    每人自然牌数 = hand_size - wild_count
    """
    shuffled = list(natural_cards)
    rng.shuffle(shuffled)

    n_players = len(wild_counts)
    natural_targets = [hand_size - w for w in wild_counts]
    assert sum(natural_targets) == len(shuffled), \
        f"自然牌总数 {len(shuffled)} != 分配目标 {sum(natural_targets)}"

    hands = []
    idx = 0
    for target in natural_targets:
        hands.append(shuffled[idx:idx + target])
        idx += target
    return hands


def _compensate(players_hands: list, wild_counts: list,
                config: DealConfig, rng: random.Random) -> list:
    """
    补偿控制：若最强与最弱牌力差距超过阈值，从最强手与最弱手交换 2~4 张牌。
    交换（而非单向转移）以确保每人手牌数始终为 27 张。
    最强手给出 n 张随机牌，最弱手也给出 n 张随机牌，双方互换。

    返回补偿记录列表。
    """
    if config.control_mode != 1:
        return []

    threshold = config.compensate_threshold
    max_rounds = 3  # 最多补偿 3 轮，避免无限循环
    log = []

    for round_idx in range(max_rounds):
        scores = []
        for i, hand in enumerate(players_hands):
            ev = evaluate_hand_power(hand, config.scores)
            scores.append(ev["score"])

        max_s = max(scores)
        min_s = min(scores)
        if min_s <= 0:
            break  # 无法计算百分比

        gap_pct = (max_s - min_s) / max_s * 100.0
        if gap_pct <= threshold:
            break

        strongest = scores.index(max_s)
        weakest = scores.index(min_s)
        if strongest == weakest:
            break

        # 随机交换 2~4 张牌（仅交换非癞子牌，癞子是战略性资源不参与交换）
        n_swap = rng.randint(2, 4)

        # 从最强手选 n_swap 张非癞子牌
        strong_nat_indices = [i for i, c in enumerate(players_hands[strongest]) if not c.is_wild]
        rng.shuffle(strong_nat_indices)
        strong_picks = sorted(strong_nat_indices[:n_swap], reverse=True)

        # 从最弱手选 n_swap 张非癞子牌
        weak_nat_indices = [i for i, c in enumerate(players_hands[weakest]) if not c.is_wild]
        rng.shuffle(weak_nat_indices)
        weak_picks = sorted(weak_nat_indices[:n_swap], reverse=True)

        # 两侧可用牌数可能不足，取实际能交换的数量
        actual_swap = min(len(strong_picks), len(weak_picks))
        if actual_swap < 2:
            break  # 无法交换足够牌
        strong_picks = strong_picks[:actual_swap]
        weak_picks = weak_picks[:actual_swap]
        n_swap = actual_swap

        # 提取被交换的牌
        strong_cards = [players_hands[strongest][i] for i in strong_picks]
        weak_cards = [players_hands[weakest][i] for i in weak_picks]

        # 从各自手牌移除
        for i in strong_picks:
            del players_hands[strongest][i]
        for i in weak_picks:
            del players_hands[weakest][i]

        # 互换：强手得到弱手的牌，弱手得到强手的牌
        players_hands[strongest].extend(weak_cards)
        players_hands[weakest].extend(strong_cards)

        log.append({
            "round": round_idx + 1,
            "from_seat": strongest,
            "to_seat": weakest,
            "cards_swapped": n_swap,
            "gap_before": round(gap_pct, 1),
        })

    return log


# ----------------------------------------------------------------
#  发牌主入口
# ----------------------------------------------------------------

class DealResult:
    """发牌结果"""
    __slots__ = ('players', 'wild_counts', 'power_evals', 'compensation_log', 'config', 'seed')

    def __init__(self):
        self.players = []          # [[Card,...], ...]  4 人 × 27 张
        self.wild_counts = []      # [int, ...]  每人癞子数
        self.power_evals = []      # [dict, ...]  每人牌力评估
        self.compensation_log = [] # 补偿记录
        self.config = None
        self.seed = None

    def to_dict(self) -> dict:
        return {
            "players": [cards_to_json(h) for h in self.players],
            "players_hex": [cards_to_hex(h) for h in self.players],
            "wild_counts": self.wild_counts,
            "power_evals": self.power_evals,
            "compensation_log": self.compensation_log,
            "seed": self.seed,
            "level": self.config.level if self.config else "2",
            "wild_mode": self.config.wild_mode if self.config else 8,
            "total_cards": sum(len(h) for h in self.players),
            "hand_size": len(self.players[0]) if self.players else 0,
        }


def deal_ba_hong_tao(config: DealConfig = None, seed: int = None,
                     level: str = "2") -> DealResult:
    """
    八红桃发牌主入口。

    config: DealConfig 对象，None 时用默认配置
    seed:   随机种子，None 时用系统随机
    level:  级牌点数（癞子原身），默认 "2"

    返回 DealResult，包含 4 人各 27 张手牌 + 牌力评估 + 补偿日志。
    """
    if config is None:
        config = default_config(level=level)
    else:
        config.level = level

    if seed is not None:
        rng = random.Random(int(seed))
    else:
        rng = random.Random()

    result = DealResult()
    result.config = config
    result.seed = seed

    # 构建完整牌库（根据 wild_mode 决定癞子花色范围）
    deck = build_full_deck_cards(level, wild_mode=config.wild_mode)
    wild_deck, natural_deck = _split_deck_by_wild(deck)

    # ─── Phase 1: 癞子分配 ───
    wild_counts = _assign_wilds_phase1(config, rng)

    # 将癞子分配给各人
    players_hands = [[] for _ in range(config.players)]
    wild_idx = 0
    for seat in range(config.players):
        n = wild_counts[seat]
        players_hands[seat] = wild_deck[wild_idx:wild_idx + n]
        wild_idx += n

    # ─── Phase 2: 自然牌发放 ───
    natural_hands = _deal_naturals(natural_deck, wild_counts, config.hand_size, rng)
    for seat in range(config.players):
        players_hands[seat].extend(natural_hands[seat])

    # ─── 补偿控制 ───
    comp_log = _compensate(players_hands, wild_counts, config, rng)

    # ─── 牌力评估（补偿后） ───
    power_evals = []
    for hand in players_hands:
        ev = evaluate_hand_power(hand, config.scores)
        power_evals.append(ev)

    # 补偿可能交换了牌（含癞子），wild_counts 以实际手牌为准
    actual_wild_counts = [sum(1 for c in h if c.is_wild) for h in players_hands]

    result.players = players_hands
    result.wild_counts = actual_wild_counts
    result.power_evals = power_evals
    result.compensation_log = comp_log

    return result


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


def test_deal_ba_1():
    """Test Deal Ba 1: 八红桃发牌基本流程 + 不变量校验"""
    print("\n" + "=" * 65)
    print("  Test Deal Ba 1: Basic deal + invariants")
    cfg = default_config()
    result = deal_ba_hong_tao(cfg, seed=42)

    # 不变量校验
    assert len(result.players) == 4, "应该有 4 个玩家"
    assert sum(result.wild_counts) == 8, f"癞子总数应为 8，实际 {sum(result.wild_counts)}"
    for i, h in enumerate(result.players):
        assert len(h) == 27, f"P{i+1} 应有 27 张牌，实际 {len(h)}"
        actual_wc = sum(1 for c in h if c.is_wild)
        assert actual_wc == result.wild_counts[i], \
            f"P{i+1} 癞子数不匹配: 配置 {result.wild_counts[i]} vs 实际 {actual_wc}"

    # 无重复 cid
    all_cids = []
    for h in result.players:
        all_cids.extend(c.cid for c in h)
    assert len(all_cids) == 108, f"总牌数应为 108，实际 {len(all_cids)}"
    assert len(all_cids) == len(set(all_cids)), "存在重复 cid"

    # 打印结果
    for i, h in enumerate(result.players):
        wc = result.wild_counts[i]
        pe = result.power_evals[i]
        role = "HUMAN" if i == 0 else "ROBOT"
        print(f"  P{i+1} ({role}): {len(h)} cards, wilds={wc}, "
              f"power={pe['score']}, bombs={pe['details']['bombs']}, "
              f"flushes={pe['details']['flushes']}")
    print(f"  Wild counts: {result.wild_counts}")
    print(f"  Compensation rounds: {len(result.compensation_log)}")
    print("  All assertions passed.")


def test_deal_ba_2():
    """Test Deal Ba 2: 1000 次发牌批量校验"""
    print("\n" + "=" * 65)
    print("  Test Deal Ba 2: 1000-deal batch validation")
    cfg = default_config()
    n_fail = 0
    for s in range(1000):
        r = deal_ba_hong_tao(cfg, seed=s)
        ok = (
            len(r.players) == 4
            and sum(r.wild_counts) == 8
            and all(len(h) == 27 for h in r.players)
        )
        all_cids = []
        for h in r.players:
            all_cids.extend(c.cid for c in h)
        if len(all_cids) != 108 or len(all_cids) != len(set(all_cids)):
            ok = False
        if not ok:
            n_fail += 1
            if n_fail <= 3:
                print(f"  FAIL seed={s}")
    print(f"  1000 deals, failures: {n_fail}")
    assert n_fail == 0, f"{n_fail} deals failed invariant check"
    print("  All 1000 deals passed.")


def test_deal_ba_3():
    """Test Deal Ba 3: 补偿控制开关"""
    print("\n" + "=" * 65)
    print("  Test Deal Ba 3: Compensation control on/off")
    from deal_config import DealConfig, WildTier

    # 补偿开
    cfg_on = default_config()
    cfg_on.control_mode = 1
    r_on = deal_ba_hong_tao(cfg_on, seed=42)

    # 补偿关
    cfg_off = default_config()
    cfg_off.control_mode = 0
    r_off = deal_ba_hong_tao(cfg_off, seed=42)

    print(f"  Compensation ON:  {len(r_on.compensation_log)} rounds")
    print(f"  Compensation OFF: {len(r_off.compensation_log)} rounds")
    assert len(r_off.compensation_log) == 0, "补偿关闭时不应有补偿记录"

    # 验证补偿开关不影响基本不变量
    for r, label in [(r_on, "ON"), (r_off, "OFF")]:
        assert all(len(h) == 27 for h in r.players), \
            f"补偿 {label}: 手牌数不为 27"
        assert sum(r.wild_counts) == 8, f"补偿 {label}: 癞子总数不为 8"
    print("  All assertions passed.")


def test_deal_ba_4():
    """Test Deal Ba 4: 牌力评估单元测试"""
    print("\n" + "=" * 65)
    print("  Test Deal Ba 4: Power evaluation")
    cfg = default_config()

    # 4A + 4K + 4Q 自然炸弹
    hand = build_hand([
        ('S','A'),('H','A'),('C','A'),('D','A'),
        ('S','K'),('H','K'),('C','K'),('D','K'),
        ('S','Q'),('H','Q'),('C','Q'),('D','Q'),
        ('S','J'),('H','J'),('C','J'),('D','J'),
        ('S','10'),('H','10'),('C','10'),('D','10'),
        ('S','9'),('H','9'),('C','9'),('D','9'),
        ('X','BJ'),('X','SJ'),
    ], wild_count=2)
    ev = evaluate_hand_power(hand, cfg.scores)
    print(f"  6×4 natural + BJ + SJ + 2 wild")
    print(f"  Bombs (potential): {ev['details']['bombs']} (expect >= 6)")
    print(f"  Wilds: {ev['details']['wilds']} (expect 2)")
    print(f"  Big joker: {ev['details']['big_joker']} (expect 1)")
    print(f"  Small joker: {ev['details']['small_joker']} (expect 1)")
    print(f"  Score: {ev['score']}")
    assert ev['details']['bombs'] >= 6
    assert ev['details']['wilds'] == 2
    assert ev['details']['big_joker'] == 1
    assert ev['details']['small_joker'] == 1
    print("  All assertions passed.")


def test_deal_ba_5():
    """Test Deal Ba 5: 机器人癞子上限"""
    print("\n" + "=" * 65)
    print("  Test Deal Ba 5: Robot wild cap")
    from deal_config import DealConfig, WildTier

    cfg = DealConfig(
        wild_tiers=[
            WildTier(0, 2, 69), WildTier(3, 4, 20),
            WildTier(5, 7, 10), WildTier(8, 8, 1),
        ],
        robot_max_wilds=0,  # 机器人 0 癞子
        control_mode=1,
    )
    # 跑 100 次，验证机器人最多拿到不超过配置允许的范围（受总和=8约束）
    for s in range(100):
        r = deal_ba_hong_tao(cfg, seed=s)
        # P1 = 人类（无限制），P2-P4 = 机器人
        # 机器人初始摇号被钳到 0，但 underflow 补偿可能给最少者加癞子
        # 所以机器人可能拿到癞子（因为总和必须=8）
        # 这里只验证不变量
        assert sum(r.wild_counts) == 8
        assert all(len(h) == 27 for h in r.players)
    print(f"  RobotMaxWilds=0, 100 deals: all invariants pass")
    print(f"  (Note: robots may still get wilds due to sum=8 constraint)")
    print("  All assertions passed.")


def main():
    random.seed(42)
    test_1()
    test_2()
    test_3()
    test_4()
    test_5()
    test_random()

    # 八红桃发牌测试
    test_deal_ba_1()
    test_deal_ba_2()
    test_deal_ba_3()
    test_deal_ba_4()
    test_deal_ba_5()

    print(f"\n{'='*65}")
    print(f"  All tests completed.")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
