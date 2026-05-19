# AI Hub UI System v0.1

> 本文档定义 Hub 前端渲染系统的实现规范：各 display 布局、Action 按钮、Task 状态机、Error 展示。
>
> - Protocol 结构见 `AI_Hub_Skill_Protocol_v0.1.md`
> - Design Token 命名见 `AI_Hub_UI_Collaboration_v0.1.md`
>
> **渲染层只读 protocol 字段，不读 `metadata`，不写 Skill 专用渲染逻辑。**

---

## 1. 渲染架构

### 1.1 组件树

```
HubContainer
├─ HubHeader
├─ HubChatStream
│    └─ [per AI turn] HubTurnGroup
│              ├─ AssistantText          ← message 字段
│              ├─ HubCardRenderer[]     ← cards[] 逐张渲染
│              └─ TaskStatusCard        ← task 存在时渲染
└─ BottomInputBar
     ├─ AttachmentPreview?
     ├─ TextInput
     ├─ AttachmentButton
     └─ SendButton
```

### 1.2 HubMessageRenderer 职责

收到一条 AI 回复（`AgentResponse`）后：

```
1. 若 error 存在            → 渲染 ErrorCard，跳过 cards
2. 若 message 存在          → 渲染 AssistantText
3. 遍历 cards[]             → 每张交给 HubCardRenderer
4. 若 task 存在             → 渲染 TaskStatusCard（与 cards 并列，不互斥）
```

**HubCardRenderer 查找规则：**

```
card.type → CardTypeRegistry[card.type] → 对应渲染组件
           ↓ 未注册
        GenericCard（title + items + actions，fallback）
```

`CardTypeRegistry` 由前端维护，映射关系：

| card.type | 组件 |
|---|---|
| `outfit_recommendation` | OutfitCard |
| `outfit_update` | OutfitUpdateCard |
| `try_on_result` | TryOnResultCard |
| `meal_log` | MealCard |
| `nutrition_summary` | NutritionSummaryCard |
| `workout_plan` | WorkoutCard |
| `workout_log` | WorkoutLogCard |
| `meeting_summary` | MeetingCard |
| *(未注册)* | GenericCard |

> **规则**：所有注册组件必须能只用 `title / subtitle / display / items / actions / status` 字段完整渲染。不得从 `metadata` 读取渲染所需字段。`metadata` 由 Skill 专用逻辑（如 deep-link 跳转、埋点）消费，渲染层透传忽略。

---

## 2. Display 布局规范

`HubCard.display` 决定 items 的排列方式，与 card.type 无关。

### 2.1 `single`

一张 Card 只有一个 item，全宽展示。

```
┌─────────────────────────────┐
│  [image 16:9]               │
│  title          subtitle    │
│  caption                    │
│  [tag] [tag]                │
│  ─────────────────────────  │
│  [Action Primary] [Ghost]   │
└─────────────────────────────┘
```

- image 宽度：全宽，高度 = 宽度 × 9/16
- actions 在 image 下方，横向排列
- 无 image 时：title 作为主体，caption 在下

### 2.2 `horizontal_carousel`

多个 item 横向滚动，每个 item 为固定宽度卡片。

```
[Item 1      ] [Item 2      ] [Item 3 ...
[image]        [image]
title          title
caption        caption
[Act] [Act]    [Act] [Act]
```

- item 宽度：屏幕宽度 × 0.72，不可拉伸
- 首张左对齐，间距 12px
- peek 下一张：右侧留 16px 空隙，提示可滑动
- 每个 item 的 actions 在 item 内部，不在 card 顶层
- card 顶层 actions（如"换一套"）放在 carousel 下方

### 2.3 `step_list`

多个 item 竖向排列，带序号，表示有顺序的步骤（如训练计划、操作流程）。

```
┌─────────────────────────────┐
│  title            subtitle  │
│  ───────────────────────    │
│  ① title                    │
│     caption                 │
│  ② title                    │
│     caption                 │
│  ③ title                    │
│     caption                 │
│  ───────────────────────    │
│  [Action Primary]           │
└─────────────────────────────┘
```

- 序号：圆形 chip，Primary 色，字号 Caption
- item 间 divider：1px Border 色
- 无图片（step_list 通常无 image，有则在 item 标题右侧缩略图 48×48）
- card 顶层 actions 放底部，跨越所有 item

### 2.4 `section_list`

多个 item 分组展示，无序号，用于摘要、列表类内容（如会议摘要、搜索结果）。

```
┌─────────────────────────────┐
│  title            subtitle  │
│  ───────────────────────    │
│  item.title                 │
│  item.caption               │
│  [tag] [tag]                │
│  ───────────────────────    │
│  item.title                 │
│  item.caption               │
│  ───────────────────────    │
│  [Action Primary] [Ghost]   │
└─────────────────────────────┘
```

- 无序号，item 之间 divider 分隔
- 支持 item 级 actions（inline，文字按钮 ghost 样式）
- card 顶层 actions 放底部

### 2.5 `grid`

多个 item 网格排列，适用于图片密集展示。

```
[img] [img]
[img] [img]
```

- 2 列，间距 8px，图片正方形（1:1）
- item 上不显示 actions，点击 item 进入详情/触发 primary action
- card 顶层 actions 放在 grid 下方

---

## 3. HubCardItem 字段渲染映射

| 字段 | 渲染位置 | 备注 |
|---|---|---|
| `image` | 卡片图片区域 | URL；缺省显示占位图 |
| `title` | 主标题，Body 字号加粗 | |
| `subtitle` | 副标题，Caption 字号，Text Secondary 色 | |
| `caption` | 图片下方或 title 下方描述，Caption 字号 | |
| `tags` | 小 chip 横排，Border 背景，Caption 字号 | 最多展示 3 个，超出截断 |
| `actions` | item 内部 Action 区（见 Section 4）| |
| `metadata` | **渲染层不读，透传忽略** | |

---

## 4. HubAction 渲染规范

### 4.1 四种 style

| style | 视觉 | 用途 |
|---|---|---|
| `primary` | 实色填充，Primary 色背景，白色文字 | 主操作（试穿、开始、保存） |
| `secondary` | 描边，Primary 色边框，Primary 色文字 | 次要操作（换一套、调整） |
| `ghost` | 无边框，Text Secondary 色文字 | 低优先级（查看详情、取消） |
| `danger` | 实色填充，Error 色背景，白色文字 | 破坏性操作（删除） |

所有按钮：
- 圆角：Small（8px）
- 内边距：水平 16px，垂直 8px
- 字号：Button Text（同 Body 字号，不加粗）
- 最小宽度：80px

### 4.2 位置规则

```
card.actions   → Card 底部，横向排列，占满宽度（等分或首个拉伸）
item.actions   → Item 内部，位于 caption/tags 下方，靠左对齐
```

- **card.actions 与 item.actions 不同时出现在同一 item 下**：item 有 actions 时，该 item 不再继承 card.actions；card.actions 仅对无 item.actions 的 items 生效（或放在 Card 底部作为全局操作）
- `horizontal_carousel` 模式：item.actions 放 item 内部；card.actions 放 carousel 下方

### 4.3 Action 触发逻辑

```
用户点击 Action 按钮
→ 前端 POST /hub/action
  {
    session_id,
    skill: card.skill,
    event: action.event,
    params: action.params,
    source: { card_id: card.id, item_id: item.id }
  }
→ 按钮进入 loading 状态（禁用 + spinner）直到收到响应
```

按钮 loading 时，同一 Card 内其他按钮也禁用，防止重复触发。

---

## 5. Task 渲染状态机

### 5.1 状态对应 UI

| status | 组件 | 交互 |
|---|---|---|
| `queued` | Skeleton + 队列文案 | 无 |
| `running` | Skeleton + 进度提示文案 | 无 |
| `completed` | 渲染 `task.result_card` | 正常 |
| `failed` | ErrorCard（task 级）+ Retry（if retryable） | Retry 按钮 |

### 5.2 TaskStatusCard 结构

```
┌─────────────────────────────┐
│  [Skill Icon] 正在处理...   │  ← task.message
│  ─────────────────────────  │
│  [████████░░░░░░░░░░░░░░░]  │  ← running 时显示，queued 时不显示
│  [Skeleton 内容占位]        │
│  [Skeleton 内容占位]        │
└─────────────────────────────┘
```

- queued 状态：只显示 Skeleton，无 progress bar
- running 状态：Skeleton + 细进度条（不确定进度时用 indeterminate 动画）
- task.message 字段作为文案，不硬编码

### 5.3 Skeleton 规范

- 背景：Card Background 色
- 高亮：Border 色，shimmer 动画（1.2s loop）
- 行高：与实际内容一致（image → 16:9 矩形；title → 单行条；caption → 双行条）
- 圆角：与实际组件一致

### 5.4 轮询规则

```
前端收到 task → 记录 task_id + poll_url + poll_interval_ms
→ 每隔 poll_interval_ms 发 GET /hub/tasks/{task_id}
→ status = completed → 用 result_card 替换 TaskStatusCard，停止轮询
→ status = failed    → 渲染 TaskErrorCard，停止轮询
→ 超过 10 分钟未完成 → 停止轮询，显示超时 ErrorCard（retryable: true）
```

- TaskStatusCard 替换为 result_card 时使用 fade-in 过渡（200ms），不闪烁

### 5.5 Task + Cards 并存

Skill 同时返回 `cards` 和 `task` 时：

```
[AssistantText]
[Card 1]
[Card 2]
[TaskStatusCard]   ← 独立占位，不插入 cards 列表中间
```

TaskStatusCard 始终排在 cards 之后。

---

## 6. Error 渲染规范

### 6.1 同步业务错误（AgentResponse.error）

`error` 存在时，不渲染 `cards`，渲染 ErrorCard：

```
┌─────────────────────────────┐
│  ⚠  error.message           │
│  [重试]                     │  ← 仅 retryable=true 时显示
└─────────────────────────────┘
```

- 背景：Error 色（低透明度填充），Border：Error 色
- 文案：直接显示 `error.message`，不拼接 `error.code`（code 用于埋点）
- 重试按钮：重新发送上一条用户消息

### 6.2 异步任务失败（HubTask.error）

Task status = `failed` 时，TaskStatusCard 变为 TaskErrorCard：

```
┌─────────────────────────────┐
│  ✕  task.error.message      │
│  [重新生成]                 │  ← retryable=true 时显示
└─────────────────────────────┘
```

- 重新生成：触发 `retry_task` event，params = `{ task_id }`
- retryable=false：只显示错误文案，无按钮

### 6.3 HubError vs HubTaskError 渲染区别

| | HubError（同步）| HubTaskError（异步） |
|---|---|---|
| 出现位置 | 替换整条 AI 回复 | 替换 TaskStatusCard |
| 重试行为 | 重发用户消息 | 触发 retry_task event |
| 影响范围 | 整条回复不渲染 | 只影响 task 区域，cards 不受影响 |

---

## 7. 消息流渲染规范

### 7.1 一条完整 AI 回复结构

```
[AssistantText]          ← 可选，0 或 1 条
[HubCard]                ← 可选，0~N 张，纵向排列
[HubCard]
[TaskStatusCard]         ← 可选，有 task 时
```

- AssistantText 和 Card 之间间距：8px
- Card 与 Card 之间间距：8px
- TaskStatusCard 与上方内容间距：8px

### 7.2 多 Card 排列

- 同一回复中多张 Card：纵向堆叠，不横向排列
- 每张 Card 宽度：消息区宽度 - 32px（左右各 16px margin）
- Card 最大高度：无限制（允许内容滚动）

### 7.3 用户消息

```
                    [用户文本气泡]  ←右对齐
                    [附件 chip(s)] ←右对齐（有附件时）
```

- 气泡：Primary 色背景，白色文字，Bubble 圆角（20px）
- 最大宽度：消息区宽度 × 0.78
- 附件 chip：图片显示缩略图 + 文件名；其他文件显示 icon + 文件名

### 7.4 AssistantText

- 左对齐，无气泡
- 字色：Text Primary
- 字号：Body
- 无 Skill icon / avatar（用户只看到一个 AI）

---

## 8. BottomInputBar & 附件预览

### 8.1 输入区结构

```
┌─────────────────────────────────┐
│  [📎]  [输入框（多行自增高）]  [▶] │
└─────────────────────────────────┘
```

- 附件按钮（📎）：触发文件选择，点击后调 `POST /hub/attachments`
- 文本框：自增高，最多 4 行，超出内部滚动
- 发送按钮：有文字或附件时 active（Primary 色），否则 disabled（Text Secondary 色）

### 8.2 附件上传中状态

```
┌─────────────────────────────────┐
│  [图片缩略图 ✕] [▶]             │
│  [💬 输入框...]                  │
└─────────────────────────────────┘
```

- 附件 chip 显示在输入框上方
- 上传中：chip 显示进度环（indeterminate）+ 文件名
- 上传完成：chip 显示缩略图（图片）或 icon（文件）+ ✕ 删除
- 上传失败：chip 显示 ⚠ + 红色边框；点击重试

### 8.3 发送携带附件

用户点击发送时，消息体携带已完成上传的 `AttachmentRef[]`；上传中的附件阻塞发送（发送按钮 disabled 直至上传完成）。

---

## 9. 禁止项

| 禁止 | 原因 |
|---|---|
| 渲染层读取 `metadata` 字段用于布局或显示 | metadata 是 Skill 专有，协议明确禁止通用 Renderer 读取 |
| 每个 Skill 注册自己的 loading 组件 | TaskStatusCard 统一，不允许 Skill 自定义 loading 样式 |
| AssistantText 输出多段 Markdown | 文案限 1-2 行，结构信息放 Card |
| Card 内嵌原始 JSON / 代码块 | 代码展示需注册专用 card.type |
| 同一 Card 同时使用 card.actions 和 item.actions 覆盖同一操作 | 见 Section 4.2，避免重复 Action |
| 前端根据 `skill` 或 `action` 字段硬编码渲染分支 | 应走 CardTypeRegistry，Skill 路径不进渲染层 |
| Task 状态枚举自行扩展 | 只用 `queued/running/completed/failed`，见协议 Section 8 |

---

## 10. 实现优先级

### P0（必须先定，否则无法分工）

1. `HubCardRenderer` + `CardTypeRegistry` 框架
2. `GenericCard` fallback 组件
3. 所有 `display` 布局组件（single / horizontal_carousel / step_list / section_list / grid）
4. `TaskStatusCard` 完整状态机（queued → running → completed/failed）
5. `ErrorCard`（同步）+ `TaskErrorCard`（异步）

### P1

1. 各 Skill 专用 Card 组件（OutfitCard / MealCard / WorkoutCard / MeetingCard）
2. `HubAction` 四种 style 统一封装
3. `AttachmentPreview` chip + 上传状态
4. Skeleton shimmer 动画

### P2

1. Card 进入动画（fade-in + slide-up）
2. TaskStatusCard → result_card 过渡动画
3. SkillPicker 组件
4. Suggestion 快捷词

---

*Renderer 读协议字段，不读 metadata，不写 Skill 名字。*
