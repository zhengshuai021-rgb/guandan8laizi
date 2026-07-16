#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八红桃发牌配置解析器
=====================
解析 BaHongTaoDealCard INI 配置段，输出结构化配置对象。

支持两种输入：
  1. INI 配置文件路径 → 用 configparser 读取
  2. INI 文本字符串 → 直接解析

配置示例：
    [BaHongTaoDealCard]
    WildTiers=0-2:69|3-4:20|5-7:10|8:1
    RobotMaxWilds=2
    ControlMode=1
    CompensateThreshold=30
    ScoreBomb=30
    ScoreSameColorLink=25
    ScoreSteelPlate=15
    ScoreLinkPair=10
    ScoreBigJoker=10
    ScoreThree=6
    ScoreSmallJoker=6
    ScoreWild=5
"""
import configparser
import io
import os
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ================================================================
#  默认配置（与配置示例完全一致）
# ================================================================

DEFAULT_INI_TEXT = """[BaHongTaoDealCard]
WildTiers=0-2:69|3-4:20|5-7:10|8:1
RobotMaxWilds=2
ControlMode=1
CompensateThreshold=30
ScoreBomb=30
ScoreSameColorLink=25
ScoreSteelPlate=15
ScoreLinkPair=10
ScoreBigJoker=10
ScoreThree=6
ScoreSmallJoker=6
ScoreWild=5
"""


# ================================================================
#  数据结构
# ================================================================

@dataclass
class WildTier:
    """癞子概率档位：[min, max] 范围 → weight"""
    min_val: int
    max_val: int
    weight: float  # 0~100 的百分数

    def contains(self, n: int) -> bool:
        return self.min_val <= n <= self.max_val

    def sample_count(self) -> int:
        """在该档位范围内均匀随机取一个整数。"""
        import random
        return random.randint(self.min_val, self.max_val)

    def __repr__(self):
        return f"WildTier({self.min_val}-{self.max_val}:{self.weight}%)"


@dataclass
class ScoreWeights:
    """牌力评分权重"""
    bomb: int = 30             # 炸弹
    same_color_link: int = 25  # 同花顺
    steel_plate: int = 15      # 钢板
    link_pair: int = 10        # 连对
    big_joker: int = 10        # 大王
    three: int = 6             # 三张
    small_joker: int = 6       # 小王
    wild: int = 5              # 癞子


@dataclass
class DealConfig:
    """八红桃发牌完整配置"""
    wild_tiers: List[WildTier] = field(default_factory=list)
    robot_max_wilds: int = 1
    control_mode: int = 1  # 0=none 1=compensate
    compensate_threshold: float = 30.0  # 百分比
    scores: ScoreWeights = field(default_factory=ScoreWeights)

    # 基本常量（一般不变）
    total_cards: int = 108
    total_wilds: int = 8
    players: int = 4
    hand_size: int = 27
    level: str = "2"  # 级牌点数（癞子的原身）

    def total_weight(self) -> float:
        return sum(t.weight for t in self.wild_tiers)

    def __repr__(self):
        return (
            f"DealConfig(\n"
            f"  wild_tiers={self.wild_tiers}\n"
            f"  robot_max_wilds={self.robot_max_wilds}\n"
            f"  control_mode={self.control_mode}\n"
            f"  compensate_threshold={self.compensate_threshold}\n"
            f"  scores={self.scores}\n"
            f")"
        )


# ================================================================
#  解析器
# ================================================================

def parse_wild_tiers(text: str) -> List[WildTier]:
    """
    解析 WildTiers 字段：
      "0-2:69|3-4:20|5-7:10|8:1"
      → [WildTier(0,2,69), WildTier(3,4,20), WildTier(5,7,10), WildTier(8,8,1)]

    支持单值简写： "8:1" 等价于 "8-8:1"
    """
    tiers = []
    if not text or not text.strip():
        return tiers

    for part in text.split("|"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"WildTier 段缺少冒号分隔符: '{part}'")
        range_str, weight_str = part.rsplit(":", 1)
        weight = float(weight_str)

        if "-" in range_str:
            lo_str, hi_str = range_str.split("-", 1)
            lo, hi = int(lo_str), int(hi_str)
        else:
            lo = hi = int(range_str)

        if lo > hi:
            lo, hi = hi, lo
        if lo < 0:
            raise ValueError(f"癞子数不能为负: '{part}'")
        tiers.append(WildTier(min_val=lo, max_val=hi, weight=weight))

    return tiers


def _strip_inline_comment(value: str) -> str:
    """去除 INI 值中的行内注释（# 开头的部分），但不影响值本身中的 #。"""
    if '#' not in value:
        return value.strip()
    # 只在 # 前面有空格时才视为注释分隔符
    idx = value.find(' #')
    if idx >= 0:
        return value[:idx].strip()
    # 行首 # 已被 configparser 过滤
    return value.strip()


def load_config_from_section(section: dict, level: str = "2") -> DealConfig:
    """
    从一个 configparser section dict 构建 DealConfig。
    section: {key: value_str, ...}（key 大小写不敏感，由调用方负责归一化）
    支持 value 中的行内注释（" # ..." 会被剥离）。
    """
    # 归一化 key 到小写 + 去除行内注释
    s = {k.lower(): _strip_inline_comment(str(v)) for k, v in section.items()}
    cfg = DealConfig(level=level)

    if "wildtiers" in s:
        cfg.wild_tiers = parse_wild_tiers(s["wildtiers"])

    if "robotmaxwilds" in s:
        cfg.robot_max_wilds = int(s["robotmaxwilds"])

    if "controlmode" in s:
        cfg.control_mode = int(s["controlmode"])

    if "compensatethreshold" in s:
        cfg.compensate_threshold = float(s["compensatethreshold"])

    sw = ScoreWeights()
    if "scorebomb" in s:
        sw.bomb = int(s["scorebomb"])
    if "scoresamecolorlink" in s:
        sw.same_color_link = int(s["scoresamecolorlink"])
    if "scoresteelplate" in s:
        sw.steel_plate = int(s["scoresteelplate"])
    if "scorelinkpair" in s:
        sw.link_pair = int(s["scorelinkpair"])
    if "scorebigjoker" in s:
        sw.big_joker = int(s["scorebigjoker"])
    if "scorethree" in s:
        sw.three = int(s["scorethree"])
    if "scoresmalljoker" in s:
        sw.small_joker = int(s["scoresmalljoker"])
    if "scorewild" in s:
        sw.wild = int(s["scorewild"])
    cfg.scores = sw

    return cfg


def load_config_from_ini_text(ini_text: str, level: str = "2",
                              section_name: str = "BaHongTaoDealCard") -> DealConfig:
    """从 INI 文本字符串解析配置。"""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # 保留原始大小写
    parser.read_string(ini_text)
    if section_name not in parser:
        raise KeyError(f"INI 中缺少 [{section_name}] 段")
    return load_config_from_section(dict(parser[section_name]), level=level)


def load_config_from_file(filepath: str, level: str = "2",
                          section_name: str = "BaHongTaoDealCard") -> DealConfig:
    """从 INI 文件路径解析配置。文件不存在时返回默认配置。"""
    if not os.path.isfile(filepath):
        return default_config(level=level)
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # 保留原始大小写
    parser.read(filepath, encoding="utf-8")
    if section_name not in parser:
        return default_config(level=level)
    return load_config_from_section(dict(parser[section_name]), level=level)


def default_config(level: str = "2") -> DealConfig:
    """返回默认配置（与 DEFAULT_INI_TEXT 一致）。"""
    return load_config_from_ini_text(DEFAULT_INI_TEXT, level=level)


# ================================================================
#  自测
# ================================================================

if __name__ == "__main__":
    cfg = default_config()
    print(cfg)
    print(f"Total weight: {cfg.total_weight()}")
    assert abs(cfg.total_weight() - 100.0) < 1e-6, "默认权重总和应为 100"
    assert len(cfg.wild_tiers) == 4
    assert cfg.wild_tiers[0].min_val == 0 and cfg.wild_tiers[0].max_val == 2
    assert cfg.wild_tiers[-1].min_val == 8 and cfg.wild_tiers[-1].max_val == 8
    assert cfg.scores.bomb == 30
    assert cfg.robot_max_wilds == 1
    print("All assertions passed.")
