# 推荐引擎 v2 + 场景引擎 设计方案

> 状态：待评审  
> 背景：博主到访（2026-05-20）后收集到的核心反馈，当前推荐引擎冷启动能力弱、生图背景场景感不足。本文提出 v2 设计，合并评审。

---

## 一、问题陈述

### 现状
- 推荐按 `(天气 + 衣橱单品 warmth/style)` 打分，无法感知场景、时间节奏、用户生活方式
- 新入库单品和旧单品权重相同，「刚买的衣服从不出现」
- 冷启动无用户历史时，推荐质量完全依赖标签准确性
- 生成图背景只有三挡（棚拍/室内/户外），与服装风格和场景脱节

### 目标
1. 冷启动即能给出「像这个人会穿的」推荐，无需 onboarding 问卷
2. 新单品在入库后一段时间内优先出现
3. 推荐图的背景场景与服装风格、使用日期匹配，有代入感
4. 随 Look 历史积累，推荐自动向真实穿搭习惯靠拢

---

## 二、推荐引擎 v2 设计

### 2.1 总体分层

```
冷启动（0 条 Look）
  └── 衣橱成分分析 → 主导风格 → 场景规则推荐

成长期（1-10 条 Look）
  └── Look 历史 → style_identity 开始有效
  └── 冷启动信号 + 历史信号混合加权

成熟期（10+ 条 Look）
  └── 历史风格加权为主，衣橱成分分析为辅

未来（硬件落地）
  └── GPS/日历行为上下文替代规则推断
```

### 2.2 冷启动：衣橱成分分析

不做 onboarding 问卷（减少摩擦），从衣橱本身推断。

**分析维度**

| 信号 | 推断内容 |
|---|---|
| 正装单品占比 > 30% | 工作日推偏 formal/通勤 |
| 正装单品占比 < 10% | 工作日不推 formal，按主导风格 |
| style 标签分布 | 计算主导风格（占比最高的 1-2 个） |
| 单品数量 < 10 件 | 降低推荐多样性，避免凑不出完整套装 |

**实现**：`outfit_recommender._infer_user_persona(items) → {dominant_styles, has_formal, wardrobe_size}`，结果写入推荐评分权重，不持久化（每次实时算）。

### 2.3 新品权重（暂缓，留存问题）

> **本期不实现。**

核心问题未解决：**`upload_time` ≠ 购买时间**。用户初次建库时批量上传的几十件旧衣服，系统无法区分哪些是刚买的、哪些是整理旧衣橱导入的。早期尤其严重。

可行方向（待后续设计）：
- 批量上传（≥ 5件/session）→ 认定旧衣，不加权
- 小批量上传（< 3件）→ 可能新买，加权
- 上传时用户主动标记「新买的」→ 最强信号

**暂时不做，避免冷启动期因全部单品「看起来都是新的」导致加权失效。**

### 2.4 场景感知：工作日 vs 周末

**原则**：不假设用户的生活方式，靠衣橱成分判断。

```
if 正装占比 > 30%:
    weekday_bias = "formal/通勤"
else:
    weekday_bias = 主导风格（不区分工作日/周末）

weekend_bias = 主导风格（轻松化，formal 降权）
```

**实现**：`recommend_outfits()` 接收 `is_weekend: bool`（由 `app.py` 根据日期注入），传入评分函数调整权重系数。

### 2.5 历史风格加权（成长期起效）

`style_identity.py` 已有，从 Look 历史推断 `style_tags`，写入 `user_profile`。

v2 增加：**组合频率加权**。统计 Look 历史中出现过的 `(category_A style, category_B style)` 组合频率，高频组合在推荐打分时获得额外加成。

```python
# 伪代码
combo_freq = Counter(
    (top_style, bottom_style)
    for look in recent_looks
    for top_style in look_top_item.style
    for bottom_style in look_bottom_item.style
)
# 推荐时：候选套装的 (top_style, bottom_style) 命中高频组合 → +bonus
```

> 评审问题 A：组合频率的样本量（Look 条数）多少才可信？建议 ≥ 5 条开始生效，< 5 条退回冷启动权重。

### 2.6 核心单品锚点（P1，不在本期）

用户在衣橱选定一件单品，AI 围绕它出搭配。单独设计，不影响本期。

---

## 三、场景引擎设计（`scene_engine.py`）

### 3.1 职责边界

```
pose_engine  → 姿势语言（power / soft / formal vibe → 站姿/动作描述）
scene_engine → 背景场景（咖啡馆 / 海边 / 办公楼外 → 背景 prompt 描述）
```

两者都由 `tryon_skill` 调用，分别生成 prompt 的不同段落，互不干扰。

### 3.2 场景信号优先级

```
1. 用户明确输入 occasion（「约会」「旅行」「派对」）→ 直接映射，最高优先
2. 无输入 → 主导风格 × 工作日/周末 → 查映射表
3. 未来：GPS/日历行为上下文替代 2
```

### 3.3 风格 × 日期 → 场景映射表

| 主导风格 | 工作日背景描述 | 周末背景描述 |
|---|---|---|
| 通勤 / 简约 / OL | modern office building entrance, glass facade, soft overcast morning light | quiet city street corner, warm café window, weekend morning |
| 法式 / 优雅 | elegant boutique-lined street, dappled afternoon sunlight, European sidewalk | cozy restaurant terrace, warm evening light, flickering candles |
| 休闲 / 街头 | creative district alleyway, street art walls, midday sun | weekend outdoor market, natural light, relaxed crowd |
| 度假 / 波西米亚 | →（少见，降级为周末处理）| beachside promenade, golden hour, soft ocean breeze |
| 运动 / 户外 | city park path, morning light filtering through trees | open trail, mountain greenery backdrop, bright midday |
| 正式 / 商务 | conference center lobby, clean marble floor, neutral white light | →（少见，降级为通勤工作日处理）|
| 极简 / 中性 | minimalist urban plaza, diffused overcast light | quiet bookstore street, soft natural light |

**Occasion 直接映射表**

| 用户输入 | 背景描述 |
|---|---|
| 约会 | romantic restaurant terrace at dusk, warm amber light |
| 旅行 / 度假 | scenic travel destination street, golden afternoon light |
| 派对 | stylish indoor venue, warm evening ambient light |
| 通勤 / 上班 | modern office building entrance, morning city light |
| 日常 / 逛街 | lively shopping street, bright daytime |
| 运动 / 健身 | open park path, fresh morning light |

### 3.4 接口设计

```python
# scene_engine.py

def pick_scene(
    items: list,           # outfit 所有单品，含 style[] 字段
    occasion: str = None,  # 用户明确输入的场合，优先级最高
    is_weekend: bool = None,  # None = 自动从今天日期判断
) -> str:
    """
    返回一段英文背景描述，直接拼入 image prompt。
    示例：'modern office building entrance, glass facade, soft overcast morning light'
    """
```

### 3.5 与推荐引擎集成

```
outfit_recommender.recommend_outfits(occasion=None)
  → outfits（含 style_tags）

tryon_skill.run_grid(user_photo, outfits, occasion=None)
  → 对每套 outfit：
      pose_hint  = pose_engine.build_pose_hint(items)
      scene_desc = scene_engine.pick_scene(items, occasion, is_weekend)
      prompt = _build_cell_prompt(items, pose_hint, scene_desc)
  → 调 image2/GPT-image 生成
```

`occasion` 从 `app.py` 透传：用户在聊天里说了场合 → `fashion_router` 提取 → 传入 `recommend_outfits` → 传入 `tryon_skill`。

---

## 四、完整数据流（v2）

```
用户请求（可含 occasion）
  │
  ▼
fashion_router.route()
  └── 提取 occasion（如有）
  │
  ▼
outfit_recommender.recommend_outfits(weather, occasion, is_weekend)
  ├── _infer_user_persona(items)       # 冷启动衣橱分析
  ├── _allowed_warmth(temp, user_id)   # 温感硬过滤
  ├── _score_item(item, persona, ...)  # 含新品加成 + 风格加权 + 历史组合加权
  └── returns outfits[]
  │
  ▼
tryon_skill.run_grid(user_photo, outfits, occasion, is_weekend)
  ├── pose_engine.build_pose_hint(items)           # 姿势
  ├── scene_engine.pick_scene(items, occasion, ...) # 背景
  └── _build_grid_prompt(outfits_data)              # 组合 prompt
  │
  ▼
GPT-image / image2 生成图
```

---

## 五、评审问题清单

| # | 问题 | 影响 |
|---|---|---|
| A | 历史组合加权最少需要多少条 Look 才可信？建议 ≥ 5 | 成长期起效时间 |
| B | 新品加成 30 天衰减是否合适？还是应该更短（14 天）？ | 新品出现频率 |
| C | 场景背景描述用中文还是英文传给 GPT-image？（目前 prompt 是中英混写）| 生成质量 |
| D | occasion 透传链路：`fashion_router` 提取 → `recommend_outfits` → `tryon_skill`，是否需要存入 session context？| 多轮对话一致性 |
| E | 工作日/周末判断只用系统日期，是否需要支持用户手动覆盖？| 自由职业/弹性工作者 |

---

## 六、不在本期范围

- 核心单品锚点搭配（P1，独立设计）
- 流行趋势输入（P2）
- GPS/日历行为上下文（硬件落地后）
- Cost per wear / 场景社交关联（P2）
