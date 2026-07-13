# 八癞子掼蛋 — 一键理牌

> 掼蛋扑克一键理牌工具，支持含 0~8 张万能牌（癞子）的 27 张手牌自动编排。
> 核心算法枚举数千种候选方案，按加权评分选出最优牌型组合。

---

## 快速开始

```bash
# 安装依赖
pip install flask

# 启动 Web 服务
python web_server.py

# 浏览器访问
# http://127.0.0.1:5001
```

运行内置测试：

```bash
python sort_8laizi.py
```

---

## 文件结构

```
guandan8laizi/
├── sort_8laizi.py   # 核心理牌算法（所有逻辑）
├── web_server.py    # Flask Web 服务器（纯壳，路由委托）
├── index.html       # 前端页面（深绿牌桌风格）
└── README.md        # 本文档
```

---

## 1. 功能概述

将玩家的 27 张手牌（含 0~8 张万能癞子牌）自动编排成最优牌型组合。目标：**减少单张散牌**、**优先组成炸弹/同花顺/顺子/木板/钢板/三带二**。

### 与普通掼蛋的关键差异

| 维度 | 普通掼蛋 | 八癞子 |
|------|----------|--------|
| 癞子数量 | 2 张（红桃级牌） | 0~8 张（全部 4 花色 × 2 副牌 = 8 张级牌） |
| 搜索空间 | ~120 种组合 | ~数千种组合 |
| 新增维度 | 无 | 癞子在炸弹/同花顺/顺子/木板/钢板/三带二之间的分配比例 |
| 同花顺 | 5 张同花连续 | 严格 5 张同花连续（不可延长） |

---

## 2. 调用链总览

```
┌─ Web UI: 点击【一键理牌】按钮
│
├─ POST /api/sort → web_server.py api_sort()
│   └─ sort_8laizi_with_details(hand_cards)
│       ├─ 分离癞子 / 自然牌
│       ├─ 枚举所有策略组合
│       │   ├─ O_flush_first       × 24排列 × (0~n_lz) bomb_wilds × wild_budgets
│       │   ├─ O_flush_single      × 24排列 × (0~n_lz) bomb_wilds × wild_budgets
│       │   ├─ N_bomb_first        × 24排列 × (0~n_lz) bomb_wilds × wild_budgets
│       │   └─ O_flush_no_straight  × 18排列 × (0~n_lz) bomb_wilds × wild_budgets
│       ├─ probe 优化：先跑不限 budget 的版本，只在实际消耗范围内枚举
│       ├─ 按 score 排序，取最优
│       └─ 三区划分 → 返回 JSON（含 hand_hex/cards_hex 编码）
│
└─ 前端: 渲染策略对比表（前端分页，每页20条）+ 三区卡牌展示
```

---

## 3. 数据结构

### 3.1 Card（单张牌）

| 字段 | 类型 | 说明 |
|------|------|------|
| `suit` | str | 花色: S/H/C/D/X |
| `rank` | str | 点数: A/2~K/SJ/BJ |
| `is_wild` | bool | 是否为癞子（级牌） |
| `power` | int | 牌点权值（2~K=value, A=14, 癞子=15, 小王=16, 大王=17） |
| `value` | int | 牌点数值（A=14, 2=2~K=13） |
| `cid` | int | 全局唯一标识（线程安全计数器自动分配） |

### 3.2 CardGroup（一组牌型）

| 字段 | 说明 |
|------|------|
| `cards` | list[Card] |
| `group_type` | king / flush / bomb / straight / board / steel / three_two / triple / pair / single |
| `power` | 牌型权值 |
| `size` | 组内张数 |

### 3.3 SortResult（一种理牌方案的结果）

```python
class SortResult:
    kings: list           # 王炸
    flushes: list         # 同花顺
    bombs: list           # 炸弹
    straights: list       # 顺子
    boards: list          # 木板（连对）
    steels: list          # 钢板（连续三张）
    three_with_twos: list # 三带二
    triples: list         # 三张
    pairs: list           # 对子
    singles: list         # 单张
```

### 3.4 三区划分

最终显示按 `炸弹区 → 非理牌区 → 理牌右区` 顺序：

| 区 | 包含牌型 | 分区规则 |
|----|---------|---------|
| 炸弹区 (sortBombs) | 王炸、同花顺、炸弹(4~10线) | 王炸→同花顺→5+线炸→4线炸 |
| 非理牌区 (notsort) | 三张、对子、单张 | 不在右区的全部归此区 |
| 理牌右区 (sortR) | 顺子(5张)、木板(6张)、钢板(6张)、三带二(5张) | 组 size 为 5 或 6 的特定牌型 |

---

## 4. 核心算法：策略枚举

### 4.1 四大策略组

| 策略组 | 提取顺序 | 说明 |
|--------|---------|------|
| **O_flush_first** | 王炸 → 同花顺 → 炸弹 → 24排列 → 三张/对子/单张 | 同花顺优先于炸弹 |
| **O_flush_single** | 同 O_flush_first，但同花顺最多取 1 个 | 避免同花顺贪心消耗过多自然牌 |
| **N_bomb_first** | 王炸 → 炸弹 → 同花顺 → 24排列 → 三张/对子/单张 | 炸弹优先于同花顺 |
| **O_flush_no_straight** | 同 O_flush_first，但去掉"顺子"提取 | 跳过顺子项（18排列） |

### 4.2 癞子预算分配（wild_budgets）

八癞子的核心创新维度。每种牌型（顺子/木板/钢板/三带二）都有一个 `max_wilds` 预算上限，控制该牌型最多消耗多少个癞子。

```python
# 4 种牌型的癞子预算组合示例（n_remaining=3）
{"straight": 2, "board": 1, "steel": 0, "three_two": 0}
{"straight": 0, "board": 0, "steel": 2, "three_two": 1}
...
```

通过 `generate_wild_budgets()` 函数枚举所有可能的分配方案。

### 4.3 probe 优化

为避免枚举无意义的 budget 组合（如给某牌型分配 5 个癞子但实际只用 1 个），采用 **probe + caps_override** 策略：

1. 对每个 `(strategy, bomb_wilds, order)` 组合，先跑一次**不限 budget** 的 probe
2. probe 返回各牌型**实际消耗的癞子数**
3. 后续只在 `[0, 实际消耗]` 范围内枚举 budget

效果：5 癞子手牌从 18144 条策略降至 ~37 条，8 癞子从 8 秒降至 0.2 秒。

### 4.4 24 种提取排列

```python
EXTRACTION_ORDERS = list(itertools.permutations(
    ["straight", "board", "steel", "three_two"]
))  # 4! = 24 种
```

---

## 5. 各牌型提取函数

### 5.1 王炸 (`extract_king_bombs`)

4 张大小王（2 副牌共 4 张 Joker）= 1 个王炸，power = 1017。

### 5.2 同花顺 (`extract_flush_straights`)

严格 5 张同花色连续牌，癞子补断口。支持 A 作为高牌（10-J-Q-K-A）。

```
10 种可能的 5 卡窗口：
  A-2-3-4-5 ~ 9-10-J-Q-K  = 9 个普通窗口
  10-J-Q-K-A               = 1 个 A 高牌窗口
```

### 5.3 炸弹 (`extract_bombs`)

分 4 个阶段贪心提取：

| 阶段 | 规则 | 消耗癞子 |
|------|------|---------|
| Phase 0 | 纯自然炸弹（≥4 张同 rank） | 0 |
| Phase 1 | 癞子补足：3张→4线, 2张→4线, 1张→4线 | ≤ max_wilds |
| Phase 2 | 已有 4+ 张自然炸弹 + 癞子 = 5+ 线炸弹 | ≤ remaining |
| Phase 3 | 纯癞子炸弹（4 癞子 = 1 炸弹） | 4/个 |

### 5.4 顺子 (`extract_straights`)

5 张连续（不限花色），癞子补断口。支持 A 低牌 (A-2-3-4-5) 和 A 高牌 (10-J-Q-K-A)。

### 5.5 木板 / 连对 (`extract_boards`)

3 个连续 rank 各有 ≥2 张（含癞子补足）。支持纯癞子补位和 Ace-high (Q-K-A)。

### 5.6 钢板 (`extract_steel_plates`)

2 个连续 rank 各有 ≥3 张（含癞子补足）。支持纯癞子补位和 Ace-high (K-A)。

### 5.7 三带二 (`extract_three_with_two`)

三张 + 对子，对子点数 ≤ J。

### 5.8 残余提取 (`extract_remaining`)

三张 → 对子 → 单张，按 rank 降序贪心。大小王 2 张同类型组对子。

---

## 6. 方案优选规则（score 函数）

所有候选方案按 `score` 元组排序，越小越好。核心是**加权综合评分**：

```python
def score(self) -> tuple:
    # ① 加权碎片分（越小越好）
    #    1 个炸弹的价值 ≈ 2.5 张牌的碎片消化量
    frag_score = (
        len(self.singles)           # 单张：权重 1.0
        + len(self.pairs) * 0.5     # 对子：权重 0.5
        + len(self.triples) * 0.3   # 三张：权重 0.3
        - len(self.bombs) * 2.5     # 炸弹：权重 -2.5（越多越好）
        - len(self.flushes) * 2.5   # 同花顺：权重 -2.5
    )
    return (
        frag_score,
        # 以下为 tiebreaker
        len(self.singles),           # 单张数
        -bomb5plus,                  # 5+线炸弹数
        -len(self.straights),        # 顺子数
        -len(self.steels),           # 钢板数
        -len(self.boards),           # 木板数
        -len(self.three_with_twos),  # 三带二数
        -len(self.triples),          # 三张数
        -len(self.pairs),            # 对子数
    )
```

**设计原理**：单纯追求"单张最少"会导致把 3 张同点牌组成三带二而非炸弹，严重削弱牌力。加权评分让"多 1 个炸弹但多 1 张单牌"的方案在接近时胜出。

---

## 7. 癞子预算配置（laiziLimit_config）

约束每种牌型在提取时最多能消耗的癞子数量，覆盖 6 种牌型：

```python
LAIZI_LIMIT_CONFIG_DEFAULT = {
    "flush":     999,   # 同花顺最多用几张癞子
    "bomb":      999,   # 炸弹最多用几张癞子
    "straight":  999,   # 顺子最多用几张癞子
    "board":     999,   # 木板最多用几张癞子
    "steel":     999,   # 钢板最多用几张癞子
    "three_two": 999,   # 三带二最多用几张癞子
}
```

### 7.1 代码调用

```python
# 禁止钢板用癞子
sort_8laizi(cards, laizi_limit={"steel": 0})

# 限制顺子最多 1 个癞子，三带二最多 2 个
sort_8laizi(cards, laizi_limit={"straight": 1, "three_two": 2})
```

### 7.2 Web UI 配置

点击页面 **⚙️ 配置项** 按钮弹出癞子预算配置窗口：

- 6 个滑块对应 6 种牌型（同花顺/炸弹/顺子/木板/钢板/三带二），每个 0~8
- 每行右侧有 **☐ 不限制** 勾选框，勾选后该牌型不受约束（滑块置灰，值显示 ∞）
- **规则**：所有项都未勾"不限制"时，滑块总和必须 = 8 才能提交
- 有任意一项勾了"不限制"时，跳过总和校验
- 配置存入 `localStorage`，刷新页面不丢失
- 点击 **↺ 重置默认** 全部归零（默认不分配癞子给任何牌型）
- 理牌时配置随 `/api/sort` 提交给后端

### 7.3 预算约束的生效层级

每种牌型的实际癞子消耗上限 = **三层取最小值**：

```
实际上限 = min(
    用户配置 (laizi_limit),        # Web UI 滑块或代码传入
    probe 探测的实际消耗量,          # 动态裁剪：贪心饱和后不再多吃
    剩余可用癞子数 (n_remaining)    # 物理上限
)
```

---

## 8. 癞子牌型多义性分析

癞子可以充当任意牌值和花色。同一组"自然牌+癞子"在不同癞子分配下可被解读为多种牌型。

### 8.1 常见多义性

| 自然牌 + 癞子 | 可解读为 |
|---|---|
| 3,4 + 3癞 | 顺子 34567 / 三带二 333+44 / 三带二 444+33 |
| 66,77 + 2癞 | 钢板 666777 / 木板 667788 |
| 3,3,3,4 + 1癞 | 三带二 333+44 / 炸弹 3333+4单 |
| ♥10,♥J + 3癞 | 同花顺 ♥10JQKA / 顺子 10JQKA / 三带二 JJJ+1010 |

### 8.2 首出 vs 压牌策略

| 场景 | 策略 |
|------|------|
| **首出** | 选牌力最强：同花顺 > 炸弹 > 顺子 > 钢板 > 木板 > 三带二 |
| **压牌** | 先找同牌型且牌力更高的解读；找不到则升级到更高牌型（如三带二→炸弹） |

---

## 9. Web API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 返回 `index.html` |
| `/api/deal` | POST | 随机发牌：27 张手牌，返回 JSON（含 `hand_hex`） |
| `/api/deck` | GET | 返回完整 108 张牌库（供配牌弹窗使用） |
| `/api/deal_custom` | POST | 自定义配牌：输入 4 个玩家的 cid 列表 |
| `/api/sort` | POST | 理牌：输入卡片列表，返回全量策略详情 + 三区划分 |

### 十六进制编码

`0x{花色}{牌值}`，逗号分隔。

| 花色 | 编码 | 牌值 | 编码 |
|------|------|------|------|
| ♦ | 1 | A | 1 |
| ♣ | 2 | 2~9 | 2~9 |
| ♥ | 3 | 10 | A |
| ♠ | 4 | J/Q/K | B/C/D |
| 王 | 5 | 小王/大王 | E/F |

示例：`0x42` = ♠2，`0x5F` = 大王

---

## 10. 公共接口（sort_8laizi 导出）

```python
sort_8laizi(hand_cards, laizi_limit=None)          # → (bombs, others)
sort_8laizi_with_details(cards, laizi_limit=None)  # → dict 含 all_results + zones
try_all_strategies(naturals, wilds, laizi_limit=None)  # → SortResult
deal_random_hand(level, seed)    # → [Card, ...]  27 张手牌
build_full_deck(level)           # → [(suit,rank,is_wild), ...]  108 张
build_full_deck_cards(level)     # → [Card, ...]  108 张带 cid
validate_deal(player_cards)      # → dict 校验自定义配牌合法性
cards_to_json(cards)             # → [{suit,rank,is_wild,cid}, ...]
card_to_hex(c)                   # → str 单张十六进制编码
cards_to_hex(cards)              # → str 逗号分隔编码
```
