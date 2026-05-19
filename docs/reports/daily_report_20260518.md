# 日报 2026-05-18

> 上半为汇报版（manager 看），下半为附录技术细节（自己/技术同事看，非必读）。

---

## 一、今日完成

### 1. 5.15 Mentor 会议决策梳理

整理并确认四条决策（见二·已决策），同时明确第 2 条「次日穿搭推荐」的实现范围：**多 agent 群聊**和**穿搭 agent 具体功能页**均需落地，不是二选一。

### 2. 前端进度核查 — TODO_20260515 三件事已全部就绪

原 TODO 标记"待完成"的三项：

| 任务 | 实际状态 |
|---|---|
| `wardrobe.html` — 衣橱浏览页 | ✅ 已完成（Tab + 3列网格 + 详情弹窗 + 美化轮询） |
| `looks.html` — 穿搭日志页 | ✅ 已完成（331 行，场景 Tab + 两种卡片） |
| `GET /api/looks` 路由 | ✅ 已完成（`app.py` 中已实现） |

TODO_20260515 Web 前端部分**全部清零**。

### 3. Tryon Skill — Pose Engine 实现

设计并实现**穿搭语言 → 姿势指令翻译层**，解决原有 tryon prompt 无姿势控制、image2 随机出姿势的问题。

- 新建 `pose_engine.py`：标签 → vibe（power / soft / formal）→ pose pool 随机采样 → 自然语言段落
- 修改 `phase2_tryon.py`：`_build_tryon_prompt()` 增加 `pose_hint` 参数，风格驱动的姿势建议替代原"不改变姿势"约束
- 支持 OOTD 上下文修正：`build_pose_hint(item, ootd_items)` 合并全套标签重新判断主导 vibe

### 4. Stream A 批量打标管线设计与实现

**背景**：同事负责 2000 件衣物的图片打标，纯手工不可行；双方通过 skill 模式交付，图片以 URL 形式传递。

**落地内容**：

- 新建 `auto_label.py`：批量调 Groq Vision 自动识别衣物属性，断点续跑（每 20 张存进度），失败图写 `auto_label_skip.json` 供人工复核；限速 2s/张（对应 Groq 30 req/min 免费额度）
- 更新 `import_batch.py`：新增 URL 模式——`image` 字段以 `http://` 开头时直接存入 DB，不再复制本地文件；原本地文件逻辑保持不变，两种交付方式兼容

**接口约定**（双方只需约定这一件事）：
```python
# 同事 skill 交付格式
def handle_input(image_url: str) -> list[dict]:
    return [{ ..., "image": "https://cdn.xxx.com/001.jpg" }]
```

### 5. 推荐展示逻辑修复 — 今日 look 确认前不切明日推荐

**问题**：晚 8 点后首页自动切换到明日推荐，但用户当天可能还没出门（today's look 未确认），体验上会显得错乱。

**修复**：
- `db.py` 新增 `has_look_on_date(date_str)` — 一条 `SELECT 1 LIMIT 1`，轻量
- `app.py` `/api/recommend` 的 cache_key 改为双条件判断：**晚 8 点后 且 今日已有确认 look → 切明日；否则保持今日**

### 6. 首页推荐 swap 功能实现

**后端**：新增 `POST /api/recommend/swap`，接收 `{item_id, outfit_item_ids}`，复用现有 `_handle_swap_item` 逻辑，秒返同品类替换结果（不调 image2）。

**前端**：每张推荐卡片 body 区增加单品缩略图行（38px），点击触发换装；有上身效果图和无效果图两种状态均可点。

### 7. Outfit 换装 popup 完整实现

完成从设计到落地的全部工作：

**前端**（`index.html`）：
- Bottom sheet 两态 HTML/CSS：状态 1（套装详情 + 单品横排）→ 状态 2（候选列表网格 + 确认按钮）
- `openOutfitSheet()` / `closeSheet()` / `showSheetDetail()` / `showCandidates()` / `selectCandidate()` / `confirmSwap()` / `_pollSwapTask()` 全套 JS 函数
- 候选列表从 `/api/wardrobe` 动态拉取，按品类过滤，当前件打 ✓ 高亮
- 确认后关闭 sheet → 异步提交 `POST /api/tasks/swap-outfit` → 每 5s 轮询 → 完成后更新卡片图片

**后端**（`app.py`）：
- 新增 `POST /api/tasks/swap-outfit`：接收 `{item_ids, new_item_id}`，后台异步调 `outfit_generator.generate_outfit_grid`，复用现有 task 基建（`_tasks` dict + `BackgroundTasks`）
- 新增 `RequestValidationError` handler：422 错误自动打印详情，便于调试

### 8. Outfit popup 两处 Bug 修复

**Bug 1 — 422 Unprocessable Entity（`new_item_id` 为 null）**

根因：`closeSheet()` 将 `_selectedCandidateId` 重置为 `null`，但在 `JSON.stringify` 构造请求体之前就已被调用，导致传入 `null`。

修复：在 `closeSheet()` 前将值捕获到局部变量 `selectedId`，后续只用 `selectedId`。

**Bug 2 — 确认换装后另外三套推荐消失**

根因：`confirmSwap()` 调用 `showAgentResponse("正在生成换装效果…")`，`#agent-response` 面板在页面底部出现，触发页面下滚，outfit 卡片随之滚出视口，用户感知为"消失"。

修复：
- 不再使用全局 `agent-response` 作为 loading 容器
- `confirmSwap()` 在目标卡片的 `outfit-visual` 上叠加 loading 遮罩（仅该卡片），同时调用 `renderOutfits()` 保持四张卡片全部在位
- `_pollSwapTask()` 完成后移除遮罩、更新该卡片图片并重渲染；其余三张卡片全程不受影响

### 9. Fashion Skill task 状态枚举对齐协议

将 `app.py` 中所有 task 状态字符串对齐 `AI_Hub_Skill_Protocol_v0.1.md` Section 8 定义：

| 位置 | 旧值 | 新值 |
|---|---|---|
| `_run_quick_tryon` / `_run_swap_outfit` 成功 | `"done"` | `"completed"` |
| 两处 task 创建初始状态 | `"running"` | `"queued"` |
| 后台函数开始执行时 | 无 | 先写 `"running"` |
| `task["error"]` | 裸字符串 | `{"code": "...", "message": "..."}` |
| `/api/recommend` status | `"ready" / "generating" / "no_photo" / "image2_offline" / "no_wardrobe"` | `"completed" / "running" / "failed"` + `error.code` |

`/api/recommend` 响应新增 `"error"` 字段（`null` 或 `{"code", "message"}`），前端可直接用 `error.code` 做分支处理，不再 hardcode 状态字符串。

### 10. Fashion Skill — AgentResponse 格式对齐（步骤 1-3）

**`fashion_dispatch.py` 完整重写**，返回格式从旧 `{action, payload, message}` 升级为标准 `AgentResponse`：

```python
{
  "skill":          "fashion",
  "action":         str,
  "message":        str,
  "cards":          list[HubCard],        # 步骤 1：结构化 Card 替代 payload
  "context_update": SkillContextPatch,    # 步骤 3：entities map
  "error":          HubError | None,
}
```

**步骤 2 — item_ids 进 metadata**：所有 outfit 的 `item_ids` 从顶层 payload 移入 `HubCardItem.metadata.item_ids`，通用 Renderer 不读 metadata，隔离 Fashion 专有字段。

**步骤 3 — context_update 输出 entities**：`_handle_recommend` 生成 `OUTFIT_000 ~ OUTFIT_N` entity_id，写入 `context_update.entities`；`_handle_swap_item` 只 patch 被换的那一个 entity。

**`app.py` `/chat` 端点同步更新**：
- `ChatResponse` 改为 `{session_id, skill, action, message, cards, context_update, error}`
- 富化逻辑（`item_images`, `item_types`）移到 `cards[].items[].metadata` 层
- context_update patch merge 写入 session，同步兼容旧 `current_item_ids` 字段
- `/api/recommend/swap` 从新格式 `cards[0].items[0].metadata` 读取结果

### 11. AI Hub 协议与 UI 系统设计完成

完成三份文档，覆盖从后端协议到前端渲染的完整链条，是移动端 App 的架构蓝图：

| 文档 | 内容 |
|---|---|
| `AI_Hub_Skill_Protocol_v0.1.md` | Hub ↔ Skill 通信协议：注册、路由、AgentResponse、Context、异步 Task、附件 |
| `AI_Hub_UI_System_v0.1.md` | 前端渲染系统：HubMessageRenderer、5 种 display 布局、Task 状态机、Error 规范 |
| `AI_Hub_UI_Collaboration_v0.1.md` | 设计语言基础：Design Token、BaseCard、消息流结构、Suggestion 原则 |

**核心设计决策：**
- 用户只看到一个 AI，多 Agent（穿搭/饮食/运动/会议）共享统一 UI，不各自设计页面
- Hub 不硬编码 Skill 业务；Skill 通过 SkillManifest 自描述，Hub 动态发现和路由
- 后端不决定 UI：Skill 返回结构化 `AgentResponse`（cards + task + context_update），前端统一渲染
- `card.type` 决定渲染组件，与 `skill/action` 无关；未注册 type 自动降级 GenericCard
- v0.1 单 Skill 路由，多 Skill 编排（Orchestrator）在 v0.2 定义

**协议压测（附录 A.1-A.4）**：Food / Fitness / Meeting 三个 Skill 均无需新增 AgentResponse 顶层字段，`metadata` 隔离 Skill 专有数据，协议对非 Fashion Skill 稳定。

**Fashion Skill 改造对照**（Section 9）：现有 `app.py` 的 `_sessions` / `/chat` 返回格式 / task 状态枚举均需对齐 Hub 协议，改造路径已明确。

### 10. 生图质量问题定位 — image2 自行添加衣橱外单品

**现象**：image2 生成的上身图里出现衣橱中不存在的外套。

**根因**：扩散模型每步去噪走的是**概率分布**，不是硬规则。Prompt 写「只穿列出的单品」只能调整权重、降低概率，无法清零——模型的训练先验（6°C 穿背心极少见）会把结果往「加外套」方向拉。

**当前缓解**：
- `grid_v1.json` prompt 加「每格只穿标注的单品，不添加任何未列出的衣物或配件」
- negative prompt 加 `no extra clothing, no added layers`
- 根本上：真实衣橱数据进来后 fallback 不触发，温度合适的单品不会触发模型补衣

**长期解法**：见二·长期方向「image2 幻觉约束」。

---

## 二、决策与讨论点

### 已决策

| # | 议题 | 最终决定 |
|---|---|---|
| 1 | 全身照上传 | 默认上传；后续考虑模拟模特图像替代 |
| 2 | 次日穿搭推荐 | 提前生成，放功能页；**多 agent 群聊 + 穿搭 agent 功能页均需实现** |
| 3 | AI Look 保存方式 | 单品拼贴截图 + 上身图，做成可滑动形式 |
| 4 | 随手试穿触发位置 | 语音和具体功能区皆可触发 |
| 5 | Swap 快速预览方案 | **不引入第三方快速推理模型**；候选列表里的预览为纯前端缩略图切换（零推理）；用户确认后触发 image2 异步生图，结果**直接更新对应推荐卡片**（不推送到聊天区，避免打断推荐视图） |
| 6 | 长期生图质量提升方向 | **检索生成（RAG for image gen）**——见下方专项说明 |
| 7 | 多 Agent 分层架构 | **Hub + Skill 模式**：Hub 统一路由、管 context 生命周期；Skill 通过 SkillManifest 自注册，返回 AgentResponse；v0.1 单 Skill 路由，v0.2 做多 Skill 编排（Orchestrator） |

### 长期方向：检索生成（Mentor 对齐）

**核心思路**：不从零 prompt 生图，而是先从图库中检索与当前推荐最相似的参考搭配，以参考图为条件生成。

```
当前流程：outfit 属性 → prompt → image2 → 生成图
RAG 流程：outfit 属性 → CLIP 检索参考图 → prompt + 参考图 → image2 → 生成图
```

**为什么能提升质量**：
- 参考图提供真实的色彩搭配、版型比例、场景氛围，image2 不再凭空发挥
- 生成结果与用户衣橱风格更一致
- 同一批参考图反复使用，生成结果更稳定（减少随机性）

**与 Stream B（CLIP 检索）的关系**：
- Stream B 的 CLIP + FAISS 基建**同时服务两个场景**：
  - OOTD 识别：摄像头帧 → 找衣橱里最像的单品
  - 生图质量：outfit embedding → 找参考图库里最像的搭配 → 作为 image2 conditioning
- 两条路复用同一套向量索引，搭建一次收益两处

**实现前提**：需积累一定量的高质量参考图库（精选搭配图）+ outfit embedding。待真实衣橱数据（同事 items.json）到位后优先建 CLIP 索引，再叠加参考图库。

### 长期方向：image2 幻觉约束

**问题本质**：扩散模型不能被硬规则约束，只能用 prompt/negative 降低概率，无法 100% 阻止模型按先验「补齐」服装。

**解法路线**（按优先级）：

| 阶段 | 方案 | 成本 |
|---|---|---|
| 近期 | 真实衣橱数据进来，fallback 不触发，温度合适单品不触发模型补衣 | 等数据 |
| 中期 | RAG 生图：用真实搭配参考图做 conditioning，参考图本来就没有多余单品 | Stream B 完成后 |
| 远期 | ControlNet 蒙版 / Inpainting：精确控制替换区域，彻底隔断模型自由发挥 | 工程量较大 |

### 待确认

1. **Hub 路由 LLM prompt 模板** — 如何用 SkillManifest 的 `description + trigger_examples` 构造 router 的 system prompt？
2. **context 过期策略** — session TTL 多久？inactive session 清理规则？
3. **callback_url 鉴权** — token 还是 HMAC signature？
4. **Fashion Skill 改造排期** — 现有 `app.py` 对齐 AgentResponse 格式，估计 0.5-1 天

---

## 三、当前状态

| 模块 | 状态 |
|---|---|
| 后端核心（router / dispatch / recommend / look / style） | ✅ |
| Web 前端 6 页 | ✅ |
| Tomorrow Planning 定时预生成 | ✅ |
| 今日 look 确认前不切明日推荐 | ✅ 今日完成 |
| Tryon Skill — Pose Engine | ✅ 今日完成 |
| Stream A 批量打标管线（`auto_label.py`） | ✅ 今日完成 |
| `import_batch.py` URL 模式 | ✅ 今日完成 |
| 首页 swap 基础功能（卡片缩略图行点击） | ✅ 今日完成 |
| Outfit popup（弹窗 + 候选列表 + 异步换装 + 卡片原位更新） | ✅ 今日完成 |
| AI Hub 协议设计（Skill Protocol + UI System + UI Collaboration） | ✅ 今日完成 |
| Hub 后端实现（注册/路由/task 管理/附件） | ❌ 待建 |
| Fashion Skill 改造 — task 状态对齐（步骤 4） | ✅ 今日完成 |
| Fashion Skill 改造 — AgentResponse 格式、cards、context_update（步骤 1-3） | ✅ 今日完成 |
| Fashion Skill 改造 — `/health` + `/manifest` 端点（步骤 5） | ✅ 今日完成 |
| Fashion Skill 改造 — 删 `_sessions`、切 callback、附件走 Hub（步骤 6-8） | ❌ 需 Hub 存在 |
| React Native App（HubContainer + Card 组件） | ❌ 待建 |
| AI Look 保存升级（可滑动 + 上身图） | ❌ 待建 |
| 真实衣橱数据接入 | ⏳ 等同事 items.json |
| `/api/looks` 重复路由清理 | ✅ 已完成（现只余 line 742） |
| CLIP 检索 + 参考图库（Stream B / RAG 基建） | ⏳ 待真实数据 |

---

## 四、阻塞 / 风险

- **真实衣橱数据**未到位，推荐质量和 CLIP 检索均无法验证；`auto_label.py` 已就绪，等同事图片链接
- **/api/looks 重复路由**（`app.py` line 435 和 670 各一个）：FastAPI 用最后注册的，功能基本一致，但需清理，避免后期混淆

---

## 五、下一步

1. **Fashion Skill 改造剩余**：`_sessions` 删除、callback_url、附件走 Hub（需等 Hub 后端）
2. **Hub 后端**：注册接口、LLM 路由、task 管理、附件代理
2. **Hub 后端**：注册接口、LLM 路由、task 管理、附件代理
3. **React Native App P0**：`HubCardRenderer` + `CardTypeRegistry` + `GenericCard` + 5 种 display 布局 + `TaskStatusCard`
4. 等同事图片链接 → `auto_label.py` 批量打标 → 入库 → 验证推荐链路
5. CLIP 检索基建（Stream B）：数据到位后建索引，为 RAG 生图铺底
6. ~~`/api/looks` 重复路由清理~~ ✅ 已完成

---
---

# 附录 — 技术细节

> 以下为实现要点与设计细节，汇报不需要看。

## A. TODO_20260515 核查结论

`wardrobe.html` 完整度超出原需求（原需求 2 列，实际 3 列；增加详情 modal、美化 badge、8 秒轮询）。`/api/looks` 在 `app.py` 中有两处定义（line 435 和 line 670），FastAPI 使用最后注册的，需合并。

## B. auto_label.py 设计要点

- **断点续跑**：`auto_label_progress.json` 记录每张图的识别结果，重跑自动跳过已完成项，中途中断零损失
- **限速**：默认 `--delay 2`（2s/张），30 张/分钟，对应 Groq 免费额度；paid 账户可调到 `--delay 0.5`
- **失败分流**：API 失败或识别返回空 → 写 `auto_label_skip.json`，跑完后只需人工处理这一批
- **2000 张预计耗时**：约 1.1 小时（delay=2）；paid 账户约 17 分钟（delay=0.5）
- **输出直接兼容** `import_batch.py`，无需中间转换

## C. import_batch.py URL 模式

```python
if image_field.startswith("http://") or image_field.startswith("https://"):
    image_url = image_field   # 直接存，不下载
elif image_field:
    # 原有本地文件逻辑
```

两种模式互不干扰，同一个 `items.json` 可以混用（部分本地 / 部分 URL）。

## D. has_look_on_date 与明日推荐切换逻辑

```python
# db.py
def has_look_on_date(date_str, user_id="default") -> bool:
    # SELECT 1 FROM look WHERE user_id=? AND date=? LIMIT 1

# app.py /api/recommend
if _now_hour >= TOMORROW_PLANNING_HOUR and db.has_look_on_date(_today.isoformat()):
    _target_date = tomorrow.isoformat()   # 切明日
else:
    _target_date = _today.isoformat()     # 保今日
```

`TOMORROW_PLANNING_HOUR = 20`，当前 hardcode，后期根据用户出行习惯动态调整（可从 outfit_log 推断用户通常何时确认当日穿搭）。

## E. /api/recommend/swap 端点设计

复用 `fashion_dispatch._handle_swap_item`，不重复实现替换逻辑：

```
POST /api/recommend/swap
{ item_id, outfit_item_ids }
→ db.get_wardrobe_item(item_id) → 取 category
→ _handle_swap_item(user_id, outfit_item_ids, {}, {category}, weather)
→ 富化 item_images → 返回
```

秒返，不调 image2；用户在 popup 确认后才走 `POST /api/tasks/swap-outfit` 异步生图。

## F. Outfit Popup 架构（已实现）

```
用户点推荐卡片
  → openOutfitSheet(idx)
      bottom sheet 弹起（状态 1：套装详情）
      outfit visual（有效果图则 <img>，无则 renderStack 2×2 拼图）
      sheet-item-row 单品横排（可点，调 showCandidates）
      [保存这套]

  → showCandidates(itemId)
      GET /api/wardrobe → 按品类过滤 → 渲染候选网格
      当前件打 ✓ 高亮
      点候选 → selectCandidate() → 启用 [确认换装]

  → confirmSwap()
      捕获 selectedId（在 closeSheet 前）
      closeSheet()
      renderOutfits(lastOutfits, lastImages, false)  // 4 张卡片全部保留
      目标卡片 outfit-visual 叠 loading 遮罩
      POST /api/tasks/swap-outfit { item_ids, new_item_id }
      → 立即返回 task_id
      → _pollSwapTask(taskId, newItemIds, swapIdx) 每 5s 轮询

  → _pollSwapTask done
      移除 loading 遮罩
      lastImages[swapIdx] = result_url
      renderOutfits()  // 目标卡片更新为新效果图，其余 3 张不变
      showAgentResponse("换装效果已生成 ✓") + [保存这套 / 再试一套]
```

`/api/tasks/swap-outfit` 与现有 `/api/tasks/quick-tryon` 共用同一套 task 基建（`_tasks` dict + `BackgroundTasks`），`kind` 字段区分类型。

## H. Outfit Popup Bug 修复记录

### H.1 422 — `new_item_id: null`

**现象**：`POST /api/tasks/swap-outfit` 返回 422，FastAPI 报 `Input should be a valid string, input: None`。

**根因**：
```javascript
// 修复前（错误）
closeSheet();                    // _selectedCandidateId → null
body: JSON.stringify({ new_item_id: _selectedCandidateId })  // null
```

**修复**：
```javascript
const selectedId = _selectedCandidateId;  // 先捕获
closeSheet();                              // 再关闭
body: JSON.stringify({ new_item_id: selectedId })  // 正确
```

### H.2 其余三套推荐消失

**现象**：确认换装后，4 张推荐卡片全部从视口消失。

**根因**：`showAgentResponse(...)` 使 `#agent-response` 面板（位于 DOM 底部）从 `display:none` 变为 `display:block`，页面内容撑高后浏览器下滚，outfit-row 滚出视口。

**修复**：去掉 `showAgentResponse` loading 调用，改为在目标卡片 `.outfit-visual` 上 `appendChild` 一个 `#swap-loading-{idx}` 遮罩，同时调 `renderOutfits` 保持全部卡片在位；任务完成后用 `querySelector` 找到遮罩移除，再调 `renderOutfits` 原位更新。

## G. RAG 生图方向技术路径

```
阶段 0（当前）：prompt → image2
阶段 1（Stream B 完成后）：outfit embedding → FAISS → Top-1 参考图 → prompt + ref_image → image2
阶段 2（参考库积累后）：多路检索（衣橱相似款 + 精选搭配图库）→ 加权融合参考 → image2
```

**参考图库建设方案**：
- 初期：用已生成的高质量 outfit 图（人工筛选）建库
- 中期：用户保存的 AI Look 自动沉淀入库（用户确认 = 质量背书）
- 长期：社区单品库（PRD P3）共享搭配图

CLIP embedding 维度：`outfit_embedding`（整套搭配）和 `item_embedding`（单品）分开存，检索时用 outfit-level 相似度匹配参考图，见 PRD v0.3 §5。

## E. Pose Engine 实现要点

### E.1 架构：pose 选择为 tryon skill 内部自动步骤

选择不暴露为独立 tool call（理由：pose 选择对 tryon 无条件必做，让 LLM 决定"是否调用"无意义；未来若需"换个姿势重新生成"再拆）。

### E.2 Vibe 三分类评分

```python
power  = len(styles & _POWER)  + (1 if {"oversize","宽松"} & fits else 0)
soft   = len(styles & _SOFT)   + (1 if "修身" in fits else 0)
formal = len(styles & _FORMAL)
```

### E.3 注入位置

原 `【人物要求】` 段"不改变姿势"替换为 `【姿势建议】` 段，含 3 条风格匹配的姿势候选，image2 从中选取。`pose_hint=None` 时降级为原逻辑，向后兼容。

---

## H. AI Hub 协议要点

### H.1 三层文档分工

```
AI_Hub_Skill_Protocol_v0.1.md   ← 后端协议：Skill 注册/路由/AgentResponse/Task/Attachment
AI_Hub_UI_System_v0.1.md        ← 前端渲染：HubMessageRenderer/display 布局/Task 状态机
AI_Hub_UI_Collaboration_v0.1.md ← 设计基础：Design Token/BaseCard/Suggestion 原则
```

### H.2 核心数据流

```
用户消息
  → POST /hub/chat（或 /hub/action）
  → Hub LLM Router（用各 Skill 的 description+trigger_examples 判断路由目标）
  → POST Skill endpoint
  → AgentResponse { skill, action, message, cards[], task?, context_update }
  → Hub patch merge context，存 task，整体返回前端
  → HubMessageRenderer → AssistantText + HubCardRenderer[] + TaskStatusCard
```

### H.3 Fashion Skill 现有代码改造清单（来自协议 Section 9）

| 当前 | 改成 |
|---|---|
| `_sessions` 字典管 `current_item_ids` | 删除；从 Hub 注入的 `context.entities` 读取 |
| `/chat` 返回 `{session_id, action, payload, message}` | 返回 `AgentResponse` |
| `payload.outfits[].item_ids` 直接暴露 | 放入 `HubCardItem.metadata.item_ids` |
| task status：`generating/no_photo/image2_offline` | 对齐 `queued/running/completed/failed`；细节进 `error.code` |
| 前端直接轮询 `/api/tasks/{id}` | Skill callback Hub → 前端轮询 `/hub/tasks/{id}` |
| 前端直传 Skill 上传图片 | `POST /hub/attachments/init` → 前端直传 → Skill 回写 complete |

### H.4 React Native App P0 实现清单

对应 `AI_Hub_UI_System_v0.1.md` Section 10 P0：

1. `HubCardRenderer` + `CardTypeRegistry`（含 GenericCard fallback）
2. 五种 display 布局组件：`single / horizontal_carousel / step_list / section_list / grid`
3. `TaskStatusCard` 完整状态机：queued（Skeleton）→ running（Skeleton + 进度条）→ completed（result_card）→ failed（ErrorCard + Retry）
4. `ErrorCard`（同步）+ `TaskErrorCard`（异步）

### H.5 协议待定事项

- Hub 路由 LLM prompt 模板（Manifest → system prompt 构造方式）
- context TTL 策略（session 过期 + inactive 清理）
- callback_url 鉴权（token / HMAC）
- 多 Skill 编排（Hub Orchestrator，v0.2）
- entities key 删除语义（v0.1 不支持，靠 TTL 过期清理）
