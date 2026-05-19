# AI Hub 设计文档

三份文档分工明确，覆盖从后端协议到前端渲染的完整链条。

---

## 文档关系

```
AI_Hub_Skill_Protocol_v0.1.md      ← 后端协议层
        ↓ 定义了 AgentResponse / HubCard / HubTask 等数据结构
AI_Hub_UI_System_v0.1.md           ← 前端渲染实现层
        ↓ 规定了组件用哪些 token、遵守哪些视觉规则
AI_Hub_UI_Collaboration_v0.1.md    ← 设计基础层
```

---

## 各文档职责

### [AI_Hub_Skill_Protocol_v0.1.md](AI_Hub_Skill_Protocol_v0.1.md)
**后端用，定义 Hub 和 Skill 之间怎么通信。**

回答的问题：
- Skill 怎么向 Hub 注册自己？
- 用户发消息 / 点 Action 按钮，Hub 怎么转发给 Skill？
- Skill 返回什么格式的数据（AgentResponse）？
- 异步任务（如图像生成）怎么走 callback？
- 附件上传走哪条路？
- session context 怎么存、怎么更新？

**受众**：各 Skill 开发者（Fashion / Food / Fitness / Meeting）、Hub 后端。

---

### [AI_Hub_UI_System_v0.1.md](AI_Hub_UI_System_v0.1.md)
**前端用，定义渲染层怎么把协议数据变成界面。**

回答的问题：
- `HubMessageRenderer` 怎么决定渲染哪个组件？
- `display: horizontal_carousel / step_list / ...` 各自长什么样？
- Action 按钮的 primary / secondary / ghost / danger 怎么画？
- Task 从 queued → running → completed / failed，每个状态界面是什么？
- 同步错误和异步任务失败，渲染有什么区别？
- 附件上传中、完成、失败各自怎么展示？

**受众**：前端开发者。不涉及设计决策，只讲实现规则。

---

### [AI_Hub_UI_Collaboration_v0.1.md](AI_Hub_UI_Collaboration_v0.1.md)
**跨角色用，定义设计语言的基础约定。**

回答的问题：
- 颜色、字号、间距、圆角的 token 叫什么、默认值是多少？
- 消息流的组件结构是什么（UserMessage / AssistantText / HubCard）？
- 有哪些 Card 类型（OutfitCard / MealCard / ...）？
- 首页 Suggestion 怎么写（写任务，不写 Agent 名字）？
- 渲染映射的原则是什么（card.type 决定组件，不按 skill/action 硬编码）？

**受众**：设计师、前端、后端都要对齐这份。是三份文档里最先要读的。

---

## 阅读顺序

```
先读 UI_Collaboration   了解设计语言和原则
再读 Skill_Protocol     了解数据结构和通信协议
最后读 UI_System        了解具体渲染实现
```

## 分工建议

| 角色 | 主要文档 |
|---|---|
| 设计师 | UI_Collaboration（补 Figma token 值）|
| Hub 后端 | Skill_Protocol（注册、路由、task 管理）|
| Skill 开发（各 Agent）| Skill_Protocol（AgentResponse 格式、Manifest 注册）|
| 前端 | 三份都要读；UI_System 是实现依据 |
