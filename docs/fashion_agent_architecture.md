# Fashion Agent 推荐系统架构文档

> 生成时间：2026-05-20  
> 覆盖文件：`fashion_dispatch.py`, `fashion_router.py`, `outfit_recommender.py`, `outfit_generator.py`, `tryon_skill.py`, `image2_client.py`, `scene_engine.py`, `pose_engine.py`

---

## 一、总体架构：四层分离

```
用户输入 "今天穿什么" / "约会穿什么"
        │
        ▼
┌──────────────────────────────┐
│  Layer 0: 意图路由            │  fashion_router.py
│  fashion_router.route()      │  LLM(Groq) + 规则兜底（关键词字典）
└──────────┬───────────────────┘
           │ dispatch key: "recommend" / "swap_item" / "quick_tryon" ...
           ▼
┌──────────────────────────────┐
│  Layer 1: 统一调度层          │  fashion_dispatch.py
│  fashion_dispatch.dispatch()  │  按路由 key 分发到各 handler
└──────────┬───────────────────┘
           │ _handle_recommend()
           ▼
┌──────────────────────────────┐
│  Layer 2: 搭配推荐层          │  outfit_recommender.py
│  recommend_outfits()         │  缓存检查 → 召回 → 组合 → 打分 → LLM caption
└──────────┬───────────────────┘
           │ 返回 outfits (n=4 套)
           ▼
┌──────────────────────────────┐
│  Layer 3: 图片生成层          │  outfit_generator.py
│  generate_outfit_grid()      │  → tryon_skill.run_grid()
│                              │  → image2_client (GPT-Image-2)
└──────────┬───────────────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌─────────┐ ┌──────────┐
│ scene   │ │ pose     │
│ engine  │ │ engine   │
└─────────┘ └──────────┘
```

---

## 二、各层详解

### Layer 0：意图路由 (`fashion_router.py`)

| Route Key | 触发条件 | 处理 |
|-----------|----------|------|
| `recommend` | "穿什么"、"搭配"、"推荐"、"天气"、"场合" | 走搭配推荐 |
| `swap_item` | "换条裤子"、"换双鞋" | 单品替换 |
| `quick_tryon` | "试穿"、"上身效果" | 随手试穿（外部图） |
| `wardrobe_query` | "衣橱"、"有几件" | 衣橱检索 |
| `save_look` | "保存"、"OOTD" | 保存搭配 |
| `unknown` | 无法判断 | 返回提示 |

**实现**：先用 Groq LLM (llama-3.3-70b) 做语义分类，失败时降级到规则关键字匹配。swap_item 会额外调用 `_extract_category()` 从文本中提取目标品类。

---

### Layer 1：统一调度 (`fashion_dispatch.py`)

`dispatch()` 获取 `fashion_router.route()` 的 key 后分发：

- **recommend** → `_handle_recommend()` → 调用 `outfit_recommender.recommend_outfits()` 拿 outfits 列表 → 调用 `outfit_generator.generate_outfit_grid()` 生成拼图 → 构建 HubCard 返回
- **swap_item** → `_handle_swap_item()` → 从当前 outfit 中找目标品类单品 → 在衣橱同品类中随机挑一件 → 秒返新单品元数据（**不调 image2**！原因是等用户确认后再生成图）
- **save_look** → 调 `look_manager.save_look()` 写入数据库
- **try_on** → `_handle_try_on()` 做前置校验（有 outfit、有全身照、image2 服务可达）后返回 `context_update` 中的私有字段给 app.py 触发后台任务

---

### Layer 2：搭配推荐引擎 (`outfit_recommender.py`)

#### 缓存策略

| 缓存层 | 数据结构 | Key | 何时失效 |
|--------|----------|-----|----------|
| 推荐结果缓存 | `_CACHE: dict` | `(user_id, date_iso, temp_bucket)` | 单品增删、STALE 标记、显式指定 occasion |
| 推荐图片缓存 | `_IMAGE_CACHE: dict` | 同上 | 推荐结果 item_ids 变化时清 |
| 生成中标记 | `_GENERATING: set` | 同上 | 不持久化，重启清零 |
| 落盘文件 | `images/recommend_cache.json` | - | 每次 `_persist()` 写入 |

- **temp_bucket 分桶**：按 5°C 分四档（cold ≤5, cool ≤15, warm ≤25, hot >25）
- **bypass_cache**：用户明确说了场合（如"约会"）时，不读不写缓存
- **缓存失效条件**：`_cache_still_valid()` 检查缓存中的 item_id 必须全在当前衣橱中

#### STALE 标记机制

`_STALE` 是第 35 行定义的全局 `set`，用来记录哪些用户的推荐缓存需要重算。

```
衣橱增删单品（app.py 3 处触发）
       │
       ▼
invalidate_outfits(user_id)     ← 第 140 行
  → _STALE.add(user_id)         ← 标记"该用户缓存已脏"
  → 注意：只标记，不清图片缓存
       │
       ▼
下次用户请求推荐时
  recommend_outfits()            ← 第 704 行
  → if user_id in _STALE:       ← 第 723 行命中
      跳过缓存，强制重算 outfits
  → _make_outfits(...)
       │
       ▼
重算完成后                       ← 第 735-738 行
  → 如果 outfits 的 item_ids 集合和旧缓存不同
    → 清 _IMAGE_CACHE（图不能复用）
  → _CACHE[cache_key] = outfits
  → _STALE.discard(user_id)     ← 清除 stale 标记
  → _persist()                  ← 落盘
```

| 操作 | `invalidate_outfits()` | `clear_cache()` |
|------|------------------------|-----------------|
| 触发 | 衣橱增删单品 | 用户更换主照 |
| outfit 缓存 | 下次请求时重算 | 立即清空 |
| 图片缓存 | **保留**（重算后 item_ids 不变则复用） | **全清** |
| STALE 标记 | 打标 | 清除标记 |

核心设计：**衣橱变了 → 推荐组合可能变 → 需要重算 outfit 列表，但 image2 调用很贵 → 如果重算后单品的 item_ids 和原来一样，旧图继续用，省掉 image2 费用。**

#### 推荐核心流程 (`_make_outfits`)

```
Stage 0: 准备权重
  ├── _allowed_warmth(temp_c, user_id)  → 温度适应的冷暖标签集合
  ├── _style_weights(user_id)           → 用户历史偏好风格权重
  ├── _occasion_boost(occasion)         → 场合对应的风格权重（如"约会"→优雅/法式+0.3）
  └── _recent_item_sets(user_id)        → 最近 7 天穿过的单品集合（去重用）

Stage 1: 各 slot 独立召回 (top-K)
  ├── tops     → _retrieve_slot(items, "上装", ...) → top 12
  ├── bottoms  → _retrieve_slot(items, "下装", ...) → top 12
  ├── fulls    → _retrieve_slot(items, "全身", ...) → top 6
  ├── outers   → _retrieve_slot(items, "外套", ...) → top 4
  └── shoes    → _retrieve_slot(items, "鞋履", ...) → top 3
  
  召回逻辑：硬过滤温度不适配单品 → 风格权重排序取 top-K
  鞋履/配件不受温度限制

Stage 2: 组合 + 打分
  ├── _form_combos()  → 笛卡尔积生成候选组合（外套仅冷天才必须）
  ├── _score_combo()  → 风格一致性 + 颜色搭配 + 去重惩罚 + 微噪音
  └── GPT-4o rerank  → rule score < 阈值 1.0 时对 top-20 用 GPT-4o 重排

Stage 2.5: 追加配件
  └── _pick_accessory()  → Jaccard 风格相似度选配件，不同套装尽量不同配件

Stage 3: LLM caption
  └── ThreadPoolExecutor 并发调 GPT-4o 给每套生成中文描述语
```

**输出**：每套 outfit = `{ item_ids, style_tags, caption, warmth_warning }`

---

### Layer 3：图片生成层 (`outfit_generator.py` → `tryon_skill.py` → `image2_client.py`)

#### grid 模式（推荐搭配用）

1. `outfit_generator.generate_outfit_grid()` 加载配置 `grid_v1.json`（cols×rows=2×2）
2. 调 `tryon_skill.run_grid()`：
   - 收集所有单品 → 从 DB 取 item dict
   - **每格独立决定场景+姿势**：遍历每套 outfit → `scene_engine.pick_scene_group()` → `pose_engine.build_pose_hint()`
   - 构建 grid prompt → 调用 `image2_client.generate()`（GPT-Image-2 images.edit）
   - 返回拼接图路径
3. `_crop_grid()` 用 PIL 按 cols×rows 均分裁切 → 各格独立 cell 图 → 返回路径列表

#### single 模式（单件试穿/换装后重生成）

- `tryon_skill.run()` → 同样走 `scene_engine` + `pose_engine` → 直接 `image2_client.generate()`

#### image2_client 实现

- 纯文生图：`images.generate` (gpt-image-2)
- 图片编辑（试穿）：`images.edit` (gpt-image-2)，图1=用户照，图2+=单品图 → 输出试穿效果
- 并发控制：`threading.Semaphore(2)` 限制同时 2 个请求（OpenAI 隐性限制）

#### tryon_skill prompt 构建

`_build_prompt()` 组装：
- **任务描述**：将图1人物试穿后续图片衣物
- **服装块**：类型/颜色/版型/穿法（从单品元数据）
- **换装范围**：全身款补全穿搭；非全身款严格保持其余部位不变
- **人物要求**：保持面部/发型/肤色/体型/全身入镜
- **姿势建议**：由 pose_engine 生成
- **场景背景**：由 scene_engine 生成
- **服装要求**：严格还原颜色/版型/面料/印花

---

## 三、场景引擎 (`scene_engine.py`) — 四层数据结构

```
用户输入 "约会" / 单品 style=["度假"] / 无关键词
        │
        ▼
┌─────────────────────────────────────────────────┐
│  第一层 _STYLE_MOOD: 风格标签 → 气质 mood       │
│  通勤→formal  法式→soft  街头→power  ...        │
│  作用：无场景关键词时兜底                         │
├─────────────────────────────────────────────────┤
│  第二层 _SCENE_HINT: 关键词 → scene_group       │
│  "约会"→date_restaurant  "度假"→travel_vacation  │
│  "派对"→party_night      "镜自拍"→home_mirror   │
│  优先级最高（occasion > style tag）              │
├─────────────────────────────────────────────────┤
│  第三层 _STYLE_TO_DEFAULT_SCENE: mood×weekend   │
│  formal/Sat→commute_street  soft/Sun→date_rest..│
│  power/Sat→weekend_market   ...                 │
├─────────────────────────────────────────────────┤
│  第四层 _SCENE_MAP: scene_group →  │
│  commute_street→"modern city street..."         │
│  date_restaurant→"rooftop restaurant..."         │
└─────────────────────────────────────────────────┘
```

**选择优先级**（`pick_scene_group`）：
1. occasion 字符串命中 `_SCENE_HINT` 的 key
2. 单品 style 标签命中 `_SCENE_HINT`
3. mood（从 style 标签推断）× weekend（从日期判断）→ 查 `_STYLE_TO_DEFAULT_SCENE`
4. 默认 `commute_street`

`pick_scene()` 把 scene_group 映射为英文背景描述字符串，直接拼入 image prompt。

**场景组对照表**：

| scene_group | 典型场景 | 背景关键词 |
|-------------|----------|-----------|
| `commute_street` | 通勤/日常/拍照 | 现代城市街道、玻璃立面、晨光 |
| `office_clean` | 办公室/职场/会议 | 大理石地板、极简中性光 |
| `date_restaurant` | 约会/餐厅/下午茶 | 黄昏屋顶、暖琥珀光、城市天际线 |
| `travel_vacation` | 旅行/度假/海边 | 金色时刻、海滩棕榈、清澈空气 |
| `weekend_market` | 逛街/市集/花市 | 户外市集、暖日光、绿树 |
| `party_night` | 派对/酒吧/晚宴/婚礼 | 深夜活动空间、暖光氛围 |
| `campus_casual` | 校园/上课/运动 | 校园步道、晨光、红砖建筑 |
| `home_mirror` | 镜自拍/试衣间 | 全身镜、柔和日光、极简背景 |

---

## 四、姿势引擎 (`pose_engine.py`) — 五层正向 + 全局负向

```
scene_group + item(style/category) + ootd_items
        │
        ▼
┌───────────────────────────────────────────────┐
│  SCENE_ACTION_ANCHORS: 场景 → 动作池(8个/场景) │
│  每个动作都是"正在干什么"，随机抽一个          │
│  动作均为中性：走路/看手机/拿咖啡/扶墨镜/...  │
│  避免：提裙摆/托腮/娇羞回眸/摸耳环/夸张造型  │
├───────────────────────────────────────────────┤
│  STYLE_MOOD_MODIFIERS: 气质 → 怎么做          │
│  power: 动作有方向感/不对称/不看镜头          │
│  soft:  动作放松/微侧/低头/轻微笑            │
│  formal: 姿态挺拔/动作克制/平静自信           │
├───────────────────────────────────────────────┤
│  CATEGORY_COMPOSITION_RULES: 品类 → 拍什么    │
│  全身→完整入镜  下装→裤脚+鞋可见              │
│  上装→领口/袖长清楚  鞋履→完整入镜...         │
├───────────────────────────────────────────────┤
│  SCENE_CAMERA_LANGUAGE: 场景 → 怎么拍         │
│  commute_street→低机位街拍   home_mirror→镜自拍│
│  party_night→夜晚氛围光   campus→朋友视角     │
├───────────────────────────────────────────────┤
│  GLOBAL_NEGATIVE_RULES (10条)                 │
│  避免僵硬站立/影楼感/夸张回眸/叉腰/裁鞋底...  │
└───────────────────────────────────────────────┘
```

**气质判断**（`_choose_mood`）：从所有单品的 style 标签中统计 power/soft/formal 三个系的数量，取最多的；全为空时默认 soft。

**品类构图约束**：`_collect_composition()` 从所有单品的 category 收集构图规则，去重后取前 5 条用分号拼接。

`build_pose_hint()` 整合以上五层，输出完整的姿势+构图+镜头 prompt 段落。

---

## 五、完整调用链路（以"约会穿什么"为例）

```
用户说 "约会穿什么"
  ↓
app.py /chat → fashion_dispatch.dispatch(user_input="约会穿什么")
  ↓
fashion_router.route("约会穿什么")
  → LLM classify → key="recommend"
  ↓
fashion_dispatch._handle_recommend(user_id, weather, context, dry_run, user_input="约会穿什么")
  ↓
outfit_recommender.recommend_outfits(user_id, weather, occasion="约会")
  ├── occasion 非空 → bypass_cache=True
  ├── _make_outfits(items, weather, occasion="约会")
  │   ├── 温度过滤 → 各 slot 召回 top-K
  │   ├── 笛卡尔积组合 → 风格/颜色/去重打分
  │   ├── GPT-4o rerank (如果规则分低)
  │   ├── 配件追加
  │   └── 并发 LLM caption
  └── 返回 4 套 outfits
  ↓
outfit_generator.generate_outfit_grid(user_photo, outfits)
  ↓
tryon_skill.run_grid(user_photo, outfits, wardrobe, cols=2, rows=2, occasion="约会")
  ├── 对每套 outfit:
  │   ├── scene_engine.pick_scene_group(items, occasion="约会")
  │   │   → "约会" 命中 _SCENE_HINT → "date_restaurant"
  │   ├── pose_engine.build_pose_hint(items[0], ootd_items, scene_group="date_restaurant")
  │   │   ├── SCENE_ACTION_ANCHORS["date_restaurant"] → 随机选动作
  │   │   ├── _choose_mood(style_tags) → "soft"
  │   │   ├── STYLE_MOOD_MODIFIERS["soft"] → 气质修饰
  │   │   ├── _collect_composition(categories) → 品类约束
  │   │   └── SCENE_CAMERA_LANGUAGE["date_restaurant"] → 镜头语言
  │   └── scene_engine.pick_scene(items, occasion="约会")
  │       → 英文背景描述 "rooftop restaurant terrace at dusk..."
  ├── _build_grid_prompt() → 构建完整 prompt
  └── image2_client.generate(prompt, image_paths)
      → GPT-Image-2 images.edit (user_photo + 单品图 → 2×2 拼图)
  ↓
_crop_grid(grid_path, 2, 2) → 裁切为 4 张独立图
  ↓
返回 4 个 cell 路径 → fashion_dispatch 构建 HubCard → app.py 返回给前端
```

---

## 六、各路由处理详情

### recommend

- 调 `recommend_outfits()` → 拿 outfits
- 调 `generate_outfit_grid()` → 拿 cell 图路径
- 构建 HubCard：`outfit_recommendation` 类型 + `horizontal_carousel` 展示
- 每张卡片含 3 个 action：试穿 / 换一件 / 保存
- context_update 中写回 entities（含 item_ids）

### swap_item

- 从 context.entities 取当前 outfit 的 item_ids
- 找目标品类单品 → 从衣橱同品类中随机换
- **秒返**，不调 image2（用户确认后由前端触发 try_on）
- 返回新 item_ids + context_update

### try_on

- 前置校验：有 outfit、有用户全身照、image2 可达
- 校验通过 → 在 context_update 中通过 `_try_on_*` 私有字段通知 app.py 创建后台任务

### wardrobe_query

- 查 DB → 返回前 20 件单品卡片

### save_look

- 取当前 item_ids → 调 `look_manager.save_look()` 写入 DB

---

## 七、文件依赖图

```
app.py
  ├── fashion_dispatch.py
  │     ├── fashion_router.py
  │     ├── outfit_recommender.py
  │     │     ├── db.py
  │     │     └── openai (GPT-4o for rerank/caption)
  │     ├── outfit_generator.py
  │     │     └── tryon_skill.py
  │     │           ├── image2_client.py  (GPT-Image-2)
  │     │           ├── scene_engine.py
  │     │           └── pose_engine.py
  │     └── look_manager.py
  └── ... (其他路由)