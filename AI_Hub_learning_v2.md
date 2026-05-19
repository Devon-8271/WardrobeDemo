
# AI Hub 学习记录 v2

# Part 1 项目目标

统一多 Agent 移动端一级对话框 UI

Agent:
- 穿搭 Agent
- 饮食 Agent
- 运动 Agent
- 会议 Agent
- 后续扩展 Agent

原则：
- Zero friction
- Image first
- Subtle AI
- 用户只看到一个 AI，不看到多个 Agent

---

# Part 2 React Native AI 学到了什么

## chat.tsx
学到：
- KeyboardAvoidingView
- 底部输入框
- Markdown
- Loading
- 消息流

结论：
消息不能只有：

user
assistant

未来：

user
assistant
card

---

## assistant.tsx

学到：

create task
↓
thread
↓
polling
↓
completed

映射：

quick_tryon
recommend
image2

统一：

TaskManager

---

## ChatModelModal.tsx

学到：

BottomSheet
+
Option选择

映射：

SkillPicker

[穿搭]
[饮食]
[运动]
[会议]

注意：

Skill是快捷入口
不是锁死模式

---

## Header.tsx

学到：

顶部保持简单

映射：

HubHeader

AI Hub
今天想让我帮你安排什么

---

## context.tsx

学到：

ThemeContext
AppContext

映射：

ThemeContext
HubContext
UserContext

---

## ChatContainer.tsx

学到：

App Shell

结构：

HubContainer
├─ Header
├─ Messages
└─ Input

---

# Part 3 Expo AI 学到了什么

## ChatUI.tsx

学到：

message.display
不是字符串

而是：

message.display
→ UI组件

映射：

OutfitCard
MealCard
WorkoutCard
MeetingCard

---

## ChatToolbarInner.tsx

学到：

Suggestion
Input
Send
Keyboard动画
Message注入

结论：

输入框
≠
输入框

它是交互中枢

---

## AssistantMessage.tsx

学到：

显示组件
≠
业务组件

---

## UserMessage.tsx

学到：

User消息简单

Assistant消息复杂

---

## FirstSuggestions.tsx

学到：

空状态不是空白

应该：

今天穿什么？
记录午餐
安排训练
总结会议

不要：

穿搭
饮食
运动

---

## Card.tsx

学到：

统一Card系统

BaseCard
├─ Header
├─ Body
├─ Actions
└─ Skeleton

---

## WeatherCard.tsx

学到：

一个主Card
+
内部横向滑动

适合：

一次推荐多套穿搭

---

## Skeleton.tsx

学到：

不要：

Loading...

应该：

结果轮廓 + shimmer

---

# Part 4 文件关系图

ChatUI
├─ ChatContainer
│
├─ MessagesScrollView
│   ├─ UserMessage
│   └─ AssistantRenderer
│
└─ ChatToolbar
     └─ FirstSuggestions

---

# Part 5 我们项目映射

HubContainer
├─ HubHeader
├─ HubChatStream
│
│   ├─ UserMessage
│   │
│   └─ HubMessageRenderer
│          ├─ TextMessage
│          ├─ OutfitCard
│          ├─ MealCard
│          ├─ WorkoutCard
│          ├─ MeetingCard
│          └─ TaskStatusCard
│
├─ BottomInputBar
│
└─ SkillPicker

---

# Part 6 下一步待研究

P0:
- ChatUI
- ChatToolbar
- Card
- Message
- Suggestions

P1:
- Skeleton
- Header
- Context

P2:
- 动画
- Theme
- Icon

---

一句话总结：

统一多 Agent UI 的核心不是聊天框，而是：

一个 Hub
+
统一 Card 系统
+
统一消息渲染
+
统一任务系统
