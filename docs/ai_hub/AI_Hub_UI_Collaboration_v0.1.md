
# AI Hub 多 Agent UI 协同规范 v0.1

## 目标

构建统一的移动端 AI Hub。

原则：

- 用户只看到一个 AI
- 多 Agent（穿搭/饮食/运动/会议）共享统一 UI 系统
- Agent 不各自设计页面
- 统一交互与视觉语言

---

# 1. 必须先统一：Design Token

统一基础视觉规则：

## 颜色

| Token | 默认值 | 用途 |
|---|---|---|
| Primary | `#1A1A1A` | 主按钮、强调色、序号 |
| Secondary | `#6B6B6B` | 次要按钮文字 |
| Background | `#F5F5F5` | 页面底色 |
| Card Background | `#FFFFFF` | Card 背景 |
| Border | `#E5E5E5` | 分割线、描边 |
| Text Primary | `#1A1A1A` | 正文、标题 |
| Text Secondary | `#8A8A8A` | 副标题、Caption |
| Success | `#22C55E` | 完成状态 |
| Warning | `#F59E0B` | 警告 |
| Error | `#EF4444` | 错误、危险按钮 |

> 以 Figma 设计稿为准，代码中统一引用 token 名，不硬编码色值。

## 字体

| Token | 字号 | 字重 | 用途 |
|---|---|---|---|
| H1 | 24px | 700 | 页面大标题 |
| H2 | 20px | 600 | 区块标题 |
| H3 | 17px | 600 | Card 标题 |
| Body | 15px | 400 | 正文、AssistantText |
| Caption | 13px | 400 | 副标题、tags、序号 |
| Button Text | 15px | 500 | 所有按钮文字 |

## 间距

统一（px）：4 / 8 / 12 / 16 / 24 / 32

## 圆角

| Token | 值 | 用途 |
|---|---|---|
| Small | 8px | 按钮、tag chip |
| Medium | 12px | Card |
| Large | 16px | 大面积容器 |
| Bubble | 20px | 用户气泡 |

---

# 2. 必须先统一：消息流结构

聊天流：

HubChat

├─ UserMessage
├─ AssistantText
└─ HubCard

规则：

用户消息：

- 右对齐
- 气泡样式

AI消息：

- 左侧
- 可包含文本
- 可包含多个 Card

规则：

一条 AI 回复：

短文本
+
0~N Card

禁止：

一个 Agent 输出很多段 markdown

---

# 3. 必须先统一：Card 系统

统一 BaseCard：

BaseCard

├─ Header
│    ├─ icon
│    └─ title
│
├─ Body
│
├─ Actions
│
└─ Status

统一：

- Card圆角
- Padding
- 标题字号
- 图片比例
- 阴影
- Divider

---

# 4. Card 类型

所有 Agent 必须基于 BaseCard 扩展

## OutfitCard

内容：

- 推荐图
- 单品
- 天气/场景

Actions：

- 试穿
- 换一套
- 保存

---

## MealCard

内容：

- 食物图
- 热量
- 营养信息

Actions：

- 记录
- 修改

---

## WorkoutCard

内容：

- 训练内容
- 时长
- 强度

Actions：

- 开始
- 调整

---

## MeetingCard

内容：

- 摘要
- Todo
- 时间

Actions：

- 加入日程
- 查看详情

---

## TaskStatusCard

内容：

- 当前状态
- Progress
- Skeleton

Actions：

- Retry
- Cancel

---

# 5. 必须先统一：渲染协议

后端禁止直接决定 UI。

Skill 统一返回 `AgentResponse`（见 `AI_Hub_Skill_Protocol_v0.1.md` Section 2.2）：

```json
{
  "skill": "fashion",
  "action": "recommend",
  "message": "给你准备了 4 套今天适合的穿搭。",
  "cards": [...],
  "task": null,
  "context_update": {...}
}
```

前端统一入口：`HubMessageRenderer(AgentResponse)`

渲染逻辑：

```
error 存在      → ErrorCard（跳过 cards）
message 存在    → AssistantText
cards[]        → 逐张查 CardTypeRegistry[card.type] → 对应组件
task 存在       → TaskStatusCard（与 cards 并列）
```

映射由 `card.type` 决定，与 `skill` / `action` 无关：

| card.type | 组件 |
|---|---|
| `outfit_recommendation` | OutfitCard |
| `meal_log` | MealCard |
| `workout_plan` | WorkoutCard |
| `meeting_summary` | MeetingCard |
| *(未注册)* | GenericCard（fallback）|

禁止：按 `skill` 或 `action` 字段硬编码渲染分支。

---

# 6. 必须先统一：Task状态

**Task 生命周期**（4 种，由 Hub 统一管理）：

| 状态 | 展示 |
|---|---|
| `queued` | Skeleton + 队列文案 |
| `running` | Skeleton + indeterminate 进度条 |
| `completed` | 渲染 `task.result_card` |
| `failed` | ErrorCard + Retry（retryable=true 时）|

> `idle` 是 Card 的展示状态（`HubCard.status`），不是 Task 状态，两者不要混用。

**Card 展示状态**（独立于 Task）：

| 状态 | 含义 |
|---|---|
| `idle` | 默认，正常可交互 |
| `stale` | 内容已过时（如 swap 后旧 Card）|
| `disabled` | 不可交互 |

禁止：每个 Agent 自己设计 loading 组件。

---

# 7. 必须先统一：Suggestion

首页：

显示：

今天穿什么？
记录午餐
安排训练
总结会议

不要：

穿搭
饮食
运动
会议

原则：

Suggestion = 用户任务

不是 Agent 名称

---

# 8. 最终结构

HubContainer

├─ HubHeader
│
├─ HubChatStream
│
│    ├─ UserMessage
│    │
│    └─ HubMessageRenderer
│             ├─ Text
│             ├─ OutfitCard
│             ├─ MealCard
│             ├─ WorkoutCard
│             ├─ MeetingCard
│             └─ TaskStatusCard
│
├─ BottomInputBar
│
└─ SkillPicker

---

# 开会优先级

P0（必须先统一）

1. Design Token
2. BaseCard
3. Message Schema
4. HubMessageRenderer
5. Task状态

P1

1. Header
2. Suggestion
3. 动画

P2

1. Theme
2. Icon
3. 微交互

---

一句话：

先统一 Card 和 Render Contract，再分工做 Agent。
