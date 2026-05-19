# AI Hub Skill Protocol v0.1

> 本文档定义 Hub 与各 Skill（Agent）之间的通信协议。
> UI 组件规范见 `AI_Hub_UI_Collaboration_v0.1.md`。

---

## 设计原则

- **Hub 不硬编码 Skill 业务**：Skill 通过 Manifest 自描述，Hub 动态发现和路由
- **两级路由**：Hub 路由到哪个 Skill；Skill 内部处理具体 Intent
- **Hub 管 context 生命周期**：Skill 只产出 `context_update`，不自己维护 session
- **后端不决定 UI**：Skill 返回结构化 Card，前端统一渲染
- **异步任务统一 Task 协议**：Skill 通过 callback 推结果，前端轮询 Hub
- **协议不含 Skill 专用字段**：所有 Skill 特有数据放进 `metadata`，核心类型保持通用
- **附件入口统一在 Hub**：前端只对接 Hub Upload API，Hub 决定 Hub-managed 还是 direct-to-skill；Skill 只读 `AttachmentRef`

---

## 1. Skill 注册

### 1.1 SkillManifest

每个 Skill 启动时向 Hub 注册。

```ts
type SkillManifest = {
  skill_id: string;           // 唯一标识，如 "fashion"
  name: string;               // 展示名，如 "穿搭助手"
  description: string;        // Hub LLM router 用于判断是否调用该 Skill
  trigger_examples: string[]; // 典型触发句，辅助 Hub 路由
  endpoint: string;           // Skill 服务地址
  health_url?: string;        // 健康检查地址，缺省为 endpoint + /health
  version: string;

  card_types: string[];       // 该 Skill 能产出的 card 类型
  task_types?: string[];      // 该 Skill 能创建的 task 类型（有异步任务时填）
  events: SkillEventSpec[];   // 该 Skill 支持的 Action event

  attachment?: {              // 附件能力声明（不支持附件的 Skill 可省略）
    supported: boolean;
    modes: ("hub_managed" | "direct_to_skill")[];
    max_size_mb?: number;
    accepted_mime_types?: string[];
    purposes?: string[];       // 如 "try_on" / "wardrobe_item" / "profile_photo"
    upload_endpoint?: string;  // direct_to_skill 时 Skill 的上传地址
  };
};

type SkillEventSpec = {
  event: string;              // 事件名，如 "swap_item"
  description?: string;
  required_params?: string[]; // 必填 param key
  optional_params?: string[]; // 选填 param key
  async?: boolean;            // 是否会产生 task
};
```

**Fashion Skill Manifest 示例：**

```json
{
  "skill_id": "fashion",
  "name": "穿搭助手",
  "description": "管理用户衣橱、推荐今日搭配、支持虚拟试穿单品",
  "trigger_examples": [
    "今天穿什么",
    "帮我搭一套",
    "换条裤子",
    "试穿这件",
    "我衣橱里有什么"
  ],
  "endpoint": "http://fashion-agent:8000",
  "health_url": "http://fashion-agent:8000/health",
  "version": "1.0",
  "card_types": ["outfit_recommendation", "outfit_update", "try_on_result"],
  "task_types": ["try_on"],
  "attachment": {
    "supported": true,
    "modes": ["hub_managed", "direct_to_skill"],
    "max_size_mb": 20,
    "accepted_mime_types": ["image/jpeg", "image/png", "image/webp"],
    "purposes": ["try_on", "wardrobe_item", "profile_photo"],
    "upload_endpoint": "/attachments/upload"
  },
  "events": [
    { "event": "swap_item",  "required_params": ["entity_id", "slot"], "async": false },
    { "event": "try_on",     "required_params": ["entity_id"],         "async": true  },
    { "event": "save_look",  "required_params": ["entity_id"],         "async": false },
    { "event": "retry_task", "required_params": ["task_id"],           "async": true  }
  ]
}
```

### 1.2 Hub 注册接口

```
POST   /hub/skills/register          注册 Skill
GET    /hub/skills                   列出所有已注册 Skill
GET    /hub/skills/{skill_id}        查询单个 Skill Manifest
GET    /hub/skills/{skill_id}/health 代理查询 Skill 健康状态
DELETE /hub/skills/{skill_id}        取消注册
```

Attachment 相关接口见 Section 6。

### 1.3 Hub 路由逻辑

Hub 收到用户消息后，用所有已注册 Skill 的 `description + trigger_examples` 交给 LLM 判断路由目标。Skill 不感知这个过程，也不需要感知其他 Skill 的存在。

路由前先调 `health_url` 验证 Skill 在线；若不可达，返回降级提示，不转发请求。

**v0.1 只支持单 Skill 路由**，每次用户消息路由到且仅路由到一个 Skill。多 Skill 编排（如"帮我查天气然后推荐穿搭"）由 Hub Orchestrator 在 v0.2 定义。

**`skill_hint` 是提示，不是强制。** 前端 SkillPicker 选中某个 Skill 后，以 `skill_hint` 传入，Hub router 参考但不强制遵守；用户自然语言意图优先。Action 按钮触发的请求（`HubActionForwardRequest`）中 `skill` 字段是明确的，Hub 直接路由，不走 LLM 分类。

---

## 2. 核心类型

### 2.1 HubToAgentRequest

Hub → Skill，两种触发路径：用户自然语言消息 / 用户点击 Action 按钮。两种路径结构不同，用联合类型区分。

```ts
type HubToAgentRequest = HubMessageRequest | HubActionForwardRequest;

// 路径一：用户发送自然语言消息
type HubMessageRequest = {
  session_id: string;
  user_id?: string;
  skill_hint?: string;   // SkillPicker 选中的 Skill；Hub router 参考但不强制，以用户意图为准
  message: string;
  context?: SkillContext;
  attachments?: AttachmentRef[];
  callback_url?: string;
};

// 路径二：用户点击 Card Action 按钮，Hub 转发给 Skill
type HubActionForwardRequest = {
  session_id: string;
  user_id?: string;
  skill: string;
  intent: string;             // 对应 SkillManifest.events[].event
  params?: Record<string, any>;
  context?: SkillContext;
  attachments?: AttachmentRef[];
  callback_url?: string;
};
```

### 2.2 AgentResponse

Skill → Hub，统一响应格式。

```ts
type AgentResponse = {
  skill: string;
  action: string;

  message?: string;                    // 短引导文案，建议 1-2 行，前端可截断
  cards?: HubCard[];                   // 0~N 张 Card，可与 task 同时存在
  task?: HubTask;                      // 异步任务；与 cards 不互斥
  context_update?: SkillContextPatch;  // Hub 做 patch merge（Partial<SkillContext>）
  error?: HubError;                    // 同步错误（非 task 失败）
  metadata?: Record<string, any>;
};

type HubError = {
  code: string;             // 机器可读错误码，如 "WARDROBE_EMPTY" / "UNSUPPORTED_FORMAT"
  message: string;          // 用户可见文案
  retryable?: boolean;
  details?: Record<string, any>;
};
```

> `cards` 与 `task` 可同时存在。例如：先展示当前搭配 Card，同时启动 try_on task。前端遇到 `task` 时必须渲染 TaskStatusCard。
>
> `error` 用于同步业务错误（如衣橱为空、格式不支持），区别于 `HubTask.error`（异步任务失败）。**`error` 存在时，前端优先渲染 ErrorCard，`cards` 通常为空；若 `retryable=true`，显示重试按钮。**

### 2.3 HubCard

```ts
type HubCard = {
  id: string;
  type: string;     // 如 "outfit_recommendation" / "outfit_update" / "try_on_result"
  skill: string;

  title: string;
  subtitle?: string;

  display:
    | "single"
    | "horizontal_carousel"
    | "step_list"
    | "section_list"
    | "grid";

  items?: HubCardItem[];
  actions?: HubAction[];         // Card 级 Action（非 item 级）

  // 注意：普通 Card 的展示状态用此字段（如 stale / disabled）
  // 异步任务状态统一用 HubTask.status，不在 Card.status 里重复表示
  status?: "idle" | "stale" | "disabled";
  metadata?: Record<string, any>;
};
```

### 2.4 HubCardItem

通用结构，Skill 专有数据放 `metadata`，不在顶层暴露。

```ts
type HubCardItem = {
  id: string;

  title?: string;
  subtitle?: string;
  image?: string;
  caption?: string;
  tags?: string[];

  actions?: HubAction[];
  metadata?: Record<string, any>;  // Skill 专用字段放这里；前端通用 Renderer 不读 metadata；字段须在 Skill 文档中声明
};
```

**Fashion 使用示例：**

```json
{
  "id": "OUTFIT_001",
  "title": "清爽通勤",
  "image": "/images/grid/cell_001.png",
  "caption": "轻松又清爽，适合今天通勤。",
  "tags": ["简约", "通勤"],
  "metadata": {
    "item_ids": ["top001", "pants001", "shoe001"],
    "item_labels": ["白色T恤", "牛仔裤", "白色运动鞋"]
  }
}
```

### 2.5 HubAction

`params` 中引用实体统一用 `entity_id`，不使用 Skill 专有 id 字段名。

```ts
type HubAction = {
  label: string;
  event: string;                    // 必须在 SkillManifest.events 中声明
  params?: Record<string, any>;     // 实体引用统一用 entity_id
  style?: "primary" | "secondary" | "ghost" | "danger";
};
```

**示例：**

```json
{ "label": "换裤子", "event": "swap_item", "params": { "entity_id": "OUTFIT_001", "slot": "bottom" }, "style": "secondary" }
{ "label": "试穿",   "event": "try_on",    "params": { "entity_id": "OUTFIT_001" }, "style": "primary" }
{ "label": "保存",   "event": "save_look", "params": { "entity_id": "OUTFIT_001" }, "style": "ghost" }
```

### 2.6 HubTask

```ts
type HubTask = {
  task_id: string;
  skill: string;
  type: string;            // 必须在 SkillManifest.task_types 中声明

  status: "queued" | "running" | "completed" | "failed";

  message?: string;
  poll_url?: string;       // 前端轮询地址，指向 Hub 端点
  poll_interval_ms?: number;

  result_card?: HubCard;   // completed 时填充
  error?: HubTaskError;
  metadata?: Record<string, any>;
};

type HubTaskError = {
  code?: string;           // 机器可读错误码，如 "IMAGE_SERVICE_TIMEOUT"
  message: string;         // 用户可见文案
  retryable?: boolean;
};
```

---

## 3. Context 协议

### 3.1 责任分工

| 模块 | 职责 |
|---|---|
| Hub | session 生命周期、context 存储、请求时注入 context |
| Skill | 读取 context、返回 context_update |

Skill 不维护 `_sessions`，不持久化 context。

### 3.2 SkillContext（通用结构）

```ts
type SkillContext = {
  current_entity_id?: string;             // 当前焦点实体
  candidate_entity_ids?: string[];        // 候选实体列表
  entities?: Record<string, SkillEntity>; // entity_id → 实体数据
  memory?: Record<string, any>;           // 其他 Skill 自定义状态
};

// context_update 是 SkillContext 的 patch，不是完整替换
type SkillContextPatch = Partial<SkillContext>;

type SkillEntity = {
  id: string;
  type: string;                    // Skill 自定义，如 "outfit" / "meal" / "meeting"
  title?: string;
  metadata?: Record<string, any>;  // Skill 专有字段
  created_at?: string;             // ISO 8601
  updated_at?: string;
  expires_at?: string;             // 短期实体（如临时试穿图）可设过期时间，Hub 负责清理
};
```

**Fashion context 示例：**

```json
{
  "current_entity_id": "OUTFIT_001",
  "candidate_entity_ids": ["OUTFIT_001", "OUTFIT_002", "OUTFIT_003"],
  "entities": {
    "OUTFIT_001": {
      "id": "OUTFIT_001",
      "type": "outfit",
      "title": "清爽通勤",
      "metadata": { "item_ids": ["top001", "pants001", "shoe001"] }
    },
    "OUTFIT_002": {
      "id": "OUTFIT_002",
      "type": "outfit",
      "title": "温柔休闲",
      "metadata": { "item_ids": ["top002", "pants002", "shoe001"] }
    }
  }
}
```

### 3.3 context_update Merge 规则

Hub 对 `context_update` 做 **patch merge**：

| 情况 | 规则 |
|---|---|
| 顶层标量字段 | 新值覆盖旧值 |
| 顶层对象字段（如 `entities`）| 按 key merge，不整体替换 |
| 字段值为 `null` | 显式清空该字段（设为 null）|
| 删除 entities 中的 key | v0.1 不支持，Hub TTL 负责过期清理 |

**示例（swap 后只更新 OUTFIT_001）：**

```json
// context_update
{
  "current_entity_id": "OUTFIT_001",
  "entities": {
    "OUTFIT_001": {
      "id": "OUTFIT_001",
      "type": "outfit",
      "metadata": { "item_ids": ["top001", "pants008", "shoe001"] }
    }
  }
}

// 结果：OUTFIT_002 保留，OUTFIT_001 被更新
```

---

## 4. Action 路由协议

用户点击 Card 上的 Action 按钮，走独立路径（不走 /chat）：

```
前端 → POST /hub/action → Hub（校验 event，注入 context）→ Skill
```

Hub 收到后：①验证 `skill` 已注册；②验证 `event` 在 Manifest 中声明；③验证 `required_params` 齐全；④注入 context；⑤转发。

### 4.1 前端发送

```ts
type HubActionRequest = {
  session_id: string;
  skill: string;
  event: string;
  params?: Record<string, any>;  // 实体引用用 entity_id

  source?: {                     // 追踪：点击来源
    card_id?: string;
    item_id?: string;
  };
};
```

**示例：**

```json
{
  "session_id": "S123",
  "skill": "fashion",
  "event": "swap_item",
  "params": { "entity_id": "OUTFIT_002", "slot": "bottom" },
  "source": { "card_id": "CARD_001", "item_id": "OUTFIT_002" }
}
```

### 4.2 Hub 转发给 Skill

```json
{
  "session_id": "S123",
  "skill": "fashion",
  "intent": "swap_item",
  "params": { "entity_id": "OUTFIT_002", "slot": "bottom" },
  "context": {
    "current_entity_id": "OUTFIT_001",
    "entities": {
      "OUTFIT_001": { "id": "OUTFIT_001", "type": "outfit", "metadata": { "item_ids": ["top001", "pants001", "shoe001"] } },
      "OUTFIT_002": { "id": "OUTFIT_002", "type": "outfit", "metadata": { "item_ids": ["top002", "pants002", "shoe001"] } }
    }
  }
}
```

Skill 从 `context.entities[params.entity_id]` 取出目标实体，完成业务逻辑。

---

## 5. 异步任务协议

适用于 image2 生图（1-5 分钟）等长时任务。

### 5.1 流程

```
Skill 收到请求
↓
立即返回 AgentResponse（含 task: queued；可同时含 cards）
↓
Hub 存储 task，整体返回给前端
↓
前端轮询 GET /hub/tasks/{task_id}
↓
Skill 后台任务完成 → POST callback_url（Hub 提供）
↓
Hub 校验 task_id + skill 匹配 → 更新 task status + result_card
↓
前端下次轮询拿到 completed + result_card
```

### 5.2 TaskCallbackRequest（Skill → Hub）

```ts
type TaskCallbackRequest = {
  task_id: string;
  skill: string;           // Hub 校验此字段必须与 task 创建时的 skill 一致
  status: "running" | "completed" | "failed";
  result_card?: HubCard;   // completed 时必填
  error?: HubTaskError;    // failed 时必填
  metadata?: Record<string, any>;
};
```

Hub 校验规则：`task_id` 必须存在；`skill` 必须与创建时一致；Skill 只能更新自己创建的 task。

### 5.3 Skill 创建任务时的响应

`cards` 与 `task` 同时存在：cards 立即渲染，TaskStatusCard 并行展示进度。

```json
{
  "skill": "fashion",
  "action": "quick_tryon",
  "message": "正在生成试穿图，需要 1-2 分钟。",
  "cards": [
    {
      "id": "CARD_CURRENT",
      "type": "outfit_preview",
      "skill": "fashion",
      "title": "当前搭配",
      "display": "single",
      "items": [...]
    }
  ],
  "task": {
    "task_id": "TASK_001",
    "skill": "fashion",
    "type": "try_on",
    "status": "queued",
    "message": "已加入队列",
    "poll_url": "/hub/tasks/TASK_001",
    "poll_interval_ms": 3000,
    "metadata": { "estimated_seconds": 90 }
  }
}
```

### 5.4 完成回调

```json
{
  "task_id": "TASK_001",
  "skill": "fashion",
  "status": "completed",
  "result_card": {
    "id": "CARD_TRYON_001",
    "type": "try_on_result",
    "skill": "fashion",
    "title": "试穿结果",
    "display": "single",
    "items": [
      {
        "id": "TRYON_001",
        "image": "/images/tryon/result_001.png",
        "actions": [
          { "label": "保存",     "event": "save_look",  "params": { "entity_id": "TRYON_001" }, "style": "primary"   },
          { "label": "重新生成", "event": "retry_task", "params": { "task_id": "TASK_001"    }, "style": "secondary" }
        ]
      }
    ]
  }
}
```

### 5.5 失败回调

```json
{
  "task_id": "TASK_001",
  "skill": "fashion",
  "status": "failed",
  "error": {
    "code": "IMAGE_SERVICE_TIMEOUT",
    "message": "图像服务暂时没有响应",
    "retryable": true
  }
}
```

---

## 6. Attachment 协议

### 6.0 Hub Attachment 接口

```
POST   /hub/attachments              上传附件（Hub-managed 模式，multipart）
POST   /hub/attachments/init         初始化上传（返回 direct-to-skill upload_url）
POST   /hub/attachments/complete     Skill 上传完成后回写 AttachmentRef
GET    /hub/attachments/{id}         查询附件元数据
```

### 6.1 原则

前端只对接 Hub，不直接感知 Skill 上传细节。Hub 支持两种内部模式，对前端透明：

| 模式 | 适用场景 |
|---|---|
| **Hub-managed** | 默认模式；会议文件、多 Skill 共享图片、需要长期保存的附件 |
| **Direct-to-Skill** | Skill 有自己的图片处理服务；大文件不想经 Hub 存储；低延迟处理（如 Fashion 试穿图） |

无论哪种模式，最终传给 Skill 的格式统一为 `AttachmentRef`，Skill 不需要关心文件实际存在哪里。

### 6.2 核心类型

```ts
type AttachmentRef = {
  attachment_id: string;
  type: "image" | "audio" | "file";
  mime_type?: string;
  filename?: string;
  storage: "hub" | "skill" | "external";
  url?: string;
  expires_at?: string;
  owner_skill?: string;
  metadata?: Record<string, any>;
};

type AttachmentUploadRequest = {
  session_id: string;
  skill_hint?: string;
  purpose?: string;      // 如 "try_on" / "wardrobe_item" / "meeting_file"
  file: File;
};
```

### 6.3 模式 A：Hub-managed（默认）

```
前端 POST /hub/attachments（multipart）
↓
Hub 保存文件，返回 AttachmentRef
↓
后续请求中 Hub 把 AttachmentRef 注入给 Skill
```

**Hub 返回示例：**

```json
{
  "attachment_id": "ATT_001",
  "type": "image",
  "mime_type": "image/jpeg",
  "filename": "full_body.jpg",
  "storage": "hub",
  "url": "/hub/files/ATT_001"
}
```

### 6.4 模式 B：Direct-to-Skill

适用于 Skill Manifest 声明支持 `direct_to_skill` 且 `purpose` 命中该 Skill 专用上传场景时。

```
前端 POST /hub/attachments/init（发 purpose + skill_hint）
↓
Hub 返回 Skill 的 upload_url + attachment_id（PENDING 状态）
↓
前端直传 Skill
↓
Skill POST /hub/attachments/complete 回写 AttachmentRef
↓
Hub 持有完整 AttachmentRef，后续请求正常注入
```

**init 返回示例：**

```json
{
  "mode": "direct_to_skill",
  "upload_url": "http://fashion-agent:8000/attachments/upload",
  "owner_skill": "fashion",
  "attachment_id": "ATT_PENDING_001"
}
```

**complete 请求（Skill → Hub）：**

```json
{
  "attachment_id": "ATT_PENDING_001",
  "owner_skill": "fashion",
  "type": "image",
  "storage": "skill",
  "url": "http://fashion-agent:8000/images/tmp/full_body.jpg",
  "metadata": { "purpose": "try_on" }
}
```

### 6.5 模式选择规则

Hub 根据 `purpose` + Skill Manifest 的 `attachment.modes` 决定，不由前端选择：

- 默认使用 Hub-managed
- 仅当目标 Skill 声明支持 `direct_to_skill` 且 `purpose` 命中其 `purposes` 列表时，才返回 direct-to-skill 流程

**Direct-to-Skill 模式下，`/hub/attachments/complete` 必须由 Skill 调用 Hub，前端不负责 complete。** 前端直传 Skill 后即完成上传操作，后续登记由 Skill 异步处理。

### 6.6 附件上传后如何进入消息

上传和发送是两个独立阶段：

```
第一阶段：前端上传附件
POST /hub/attachments（或 /hub/attachments/init + 直传 Skill）
↓ 拿到 AttachmentRef（attachment_id + url）

第二阶段：用户发送消息时携带 AttachmentRef
```

**HubMessageRequest 携带附件示例：**

```json
{
  "session_id": "S123",
  "skill": "fashion",
  "message": "用这张全身照帮我试穿",
  "attachments": [
    {
      "attachment_id": "ATT_001",
      "type": "image",
      "storage": "hub",
      "url": "/hub/files/ATT_001",
      "mime_type": "image/jpeg"
    }
  ]
}
```

Skill 收到后读 `attachments[0].url` 处理图片，不需要关心文件存在 Hub 还是 Skill 自己的存储里。

---

## 7. 完整流程示例

### 7.1 推荐搭配（recommend）

```json
{
  "skill": "fashion",
  "action": "recommend",
  "message": "给你准备了 4 套今天适合的穿搭。",
  "cards": [
    {
      "id": "CARD_001",
      "type": "outfit_recommendation",
      "skill": "fashion",
      "title": "今日穿搭推荐",
      "subtitle": "通勤 · 22°C",
      "display": "horizontal_carousel",
      "items": [
        {
          "id": "OUTFIT_001",
          "title": "清爽通勤",
          "image": "/images/grid/cell_001.png",
          "caption": "轻松又清爽，适合今天通勤。",
          "tags": ["简约", "通勤"],
          "metadata": { "item_ids": ["top001", "pants001", "shoe001"], "item_labels": ["白色T恤", "牛仔裤", "白色运动鞋"] },
          "actions": [
            { "label": "试穿",   "event": "try_on",    "params": { "entity_id": "OUTFIT_001" },               "style": "primary"   },
            { "label": "换裤子", "event": "swap_item", "params": { "entity_id": "OUTFIT_001", "slot": "bottom" }, "style": "secondary" },
            { "label": "保存",   "event": "save_look", "params": { "entity_id": "OUTFIT_001" },               "style": "ghost"     }
          ]
        },
        {
          "id": "OUTFIT_002",
          "title": "温柔休闲",
          "image": "/images/grid/cell_002.png",
          "caption": "随性又有质感，适合周末出行。",
          "tags": ["休闲", "法式"],
          "metadata": { "item_ids": ["top002", "pants002", "shoe001"], "item_labels": ["米色针织", "卡其裤", "白色运动鞋"] },
          "actions": [
            { "label": "试穿",   "event": "try_on",    "params": { "entity_id": "OUTFIT_002" },               "style": "primary"   },
            { "label": "换裤子", "event": "swap_item", "params": { "entity_id": "OUTFIT_002", "slot": "bottom" }, "style": "secondary" },
            { "label": "保存",   "event": "save_look", "params": { "entity_id": "OUTFIT_002" },               "style": "ghost"     }
          ]
        }
      ]
    }
  ],
  "context_update": {
    "current_entity_id": "OUTFIT_001",
    "candidate_entity_ids": ["OUTFIT_001", "OUTFIT_002", "OUTFIT_003", "OUTFIT_004"],
    "entities": {
      "OUTFIT_001": { "id": "OUTFIT_001", "type": "outfit", "metadata": { "item_ids": ["top001", "pants001", "shoe001"] } },
      "OUTFIT_002": { "id": "OUTFIT_002", "type": "outfit", "metadata": { "item_ids": ["top002", "pants002", "shoe001"] } },
      "OUTFIT_003": { "id": "OUTFIT_003", "type": "outfit", "metadata": { "item_ids": ["top003", "pants001", "shoe002"] } },
      "OUTFIT_004": { "id": "OUTFIT_004", "type": "outfit", "metadata": { "item_ids": ["top001", "pants003", "shoe001"] } }
    }
  }
}
```

### 7.2 换单品（swap_item）

**前端 Action 请求：**

```json
{
  "session_id": "S123",
  "skill": "fashion",
  "event": "swap_item",
  "params": { "entity_id": "OUTFIT_002", "slot": "bottom" },
  "source": { "card_id": "CARD_001", "item_id": "OUTFIT_002" }
}
```

**Skill 响应：**

```json
{
  "skill": "fashion",
  "action": "swap_item",
  "message": "我帮你把第二套的裤子换成了更清爽的款式。",
  "cards": [
    {
      "id": "CARD_002",
      "type": "outfit_update",
      "skill": "fashion",
      "title": "已更新第二套搭配",
      "display": "single",
      "items": [
        {
          "id": "OUTFIT_002",
          "title": "温柔休闲 · 更新版",
          "metadata": { "item_ids": ["top002", "pants008", "shoe001"], "item_labels": ["米色针织", "浅色阔腿裤", "白色运动鞋"] },
          "actions": [
            { "label": "保存", "event": "save_look", "params": { "entity_id": "OUTFIT_002" }, "style": "primary" }
          ]
        }
      ]
    }
  ],
  "context_update": {
    "current_entity_id": "OUTFIT_002",
    "entities": {
      "OUTFIT_002": { "id": "OUTFIT_002", "type": "outfit", "metadata": { "item_ids": ["top002", "pants008", "shoe001"] } }
    }
  }
}
```

---

## 8. Task Status 统一枚举

前端和 Hub 统一用以下状态，Skill 不自定义：

| Status | 含义 | 前端展示 |
|---|---|---|
| `queued` | 已收到，排队中 | Skeleton + 队列提示 |
| `running` | 执行中 | Skeleton + Progress |
| `completed` | 完成 | 渲染 result_card |
| `failed` | 失败 | ErrorCard + Retry（if retryable）|

诊断信息（如 `IMAGE_SERVICE_TIMEOUT` / `NO_USER_PHOTO`）放进 `error.code`，不扩展状态枚举。

---

## 9. Fashion Skill 现有代码改造对照

| 当前 | 改成 |
|---|---|
| `_sessions` 字典管 `current_item_ids` | 删除；从 Hub 注入的 `context.entities` 读取 |
| `/chat` 返回 `{session_id, action, payload, message}` | 返回 `AgentResponse`（`skill/action/message/cards/context_update`）|
| `payload.outfits[].item_ids` 直接暴露 | 放入 `HubCardItem.metadata.item_ids` |
| `FashionContext.outfit_map` | 改为 `SkillContext.entities`（每个 outfit 是一个 SkillEntity）|
| Action params 用 `outfit_id` | 改为 `entity_id` |
| task status：`generating/no_photo/image2_offline` | 对齐 `queued/running/completed/failed`；细节进 `error.code` |
| 前端直接轮询 `/api/tasks/{id}` | Skill 回调 Hub（`POST callback_url`）；前端轮询 `/hub/tasks/{id}` |
| 前端直传 Skill 上传图片 | 改为 `POST /hub/attachments/init` → 前端直传 → Skill 回写 complete |

---

## 附录：多 Skill 压测示例

> 目的：验证协议核心类型（AgentResponse、HubCard、SkillContext、context_update）在非 Fashion Skill 下无需新增顶层字段，Skill 专有数据全部收进 `metadata`。
>
> 以下为极简 mock，不代表完整实现，仅压测协议边界。

---

### A.1 Food Skill — 记录午餐

**触发：** 用户说"我刚吃了一碗番茄鸡蛋面"

**Skill Manifest（关键字段）：**

```json
{
  "skill_id": "food",
  "name": "饮食记录",
  "description": "记录用户每日饮食，分析热量和营养摄入",
  "trigger_examples": ["记录午餐", "我刚吃了", "今天吃了什么", "帮我算一下热量"],
  "card_types": ["meal_log", "nutrition_summary"],
  "task_types": [],
  "attachment": {
    "supported": true,
    "modes": ["hub_managed"],
    "accepted_mime_types": ["image/jpeg", "image/png"],
    "purposes": ["meal_photo"]
  },
  "events": [
    { "event": "edit_meal",   "required_params": ["entity_id"], "async": false },
    { "event": "delete_meal", "required_params": ["entity_id"], "async": false }
  ]
}
```

**AgentResponse：**

```json
{
  "skill": "food",
  "action": "log_meal",
  "message": "已记录，今天午餐摄入 520 kcal。",
  "cards": [
    {
      "id": "CARD_MEAL_001",
      "type": "meal_log",
      "skill": "food",
      "title": "午餐",
      "subtitle": "12:35 · 520 kcal",
      "display": "single",
      "items": [
        {
          "id": "MEAL_001",
          "title": "番茄鸡蛋面",
          "caption": "碳水 72g · 蛋白 18g · 脂肪 8g",
          "tags": ["主食", "低脂"],
          "metadata": {
            "meal_type": "lunch",
            "calories": 520,
            "nutrients": { "carbs_g": 72, "protein_g": 18, "fat_g": 8 },
            "logged_at": "2026-05-18T12:35:00Z"
          },
          "actions": [
            { "label": "修改", "event": "edit_meal",   "params": { "entity_id": "MEAL_001" }, "style": "secondary" },
            { "label": "删除", "event": "delete_meal", "params": { "entity_id": "MEAL_001" }, "style": "danger"    }
          ]
        }
      ]
    }
  ],
  "context_update": {
    "current_entity_id": "MEAL_001",
    "entities": {
      "MEAL_001": {
        "id": "MEAL_001",
        "type": "meal",
        "title": "番茄鸡蛋面",
        "metadata": { "calories": 520, "meal_type": "lunch" },
        "created_at": "2026-05-18T12:35:00Z"
      }
    }
  }
}
```

**压测结论：** Food Skill 用 `entity type: meal`，热量/营养全进 `metadata`，无需新增 AgentResponse 顶层字段。

---

### A.2 Fitness Skill — 推荐今日训练

**触发：** 用户说"帮我安排今天的训练"

**Skill Manifest（关键字段）：**

```json
{
  "skill_id": "fitness",
  "name": "运动助手",
  "description": "根据用户目标和当日状态推荐训练计划",
  "trigger_examples": ["安排训练", "今天练什么", "运动计划", "帮我健身"],
  "card_types": ["workout_plan", "workout_log"],
  "task_types": [],
  "events": [
    { "event": "start_workout",  "required_params": ["entity_id"], "async": false },
    { "event": "adjust_workout", "required_params": ["entity_id"], "async": false }
  ]
}
```

**AgentResponse：**

```json
{
  "skill": "fitness",
  "action": "suggest_workout",
  "message": "今天适合力量训练，预计 45 分钟。",
  "cards": [
    {
      "id": "CARD_WORKOUT_001",
      "type": "workout_plan",
      "skill": "fitness",
      "title": "今日训练",
      "subtitle": "力量 · 45 min · 中等强度",
      "display": "step_list",
      "items": [
        {
          "id": "EXERCISE_001",
          "title": "深蹲",
          "caption": "4 组 × 12 次 · 休息 60s",
          "tags": ["腿部", "核心"],
          "metadata": { "sets": 4, "reps": 12, "rest_seconds": 60, "muscle_group": "legs" }
        },
        {
          "id": "EXERCISE_002",
          "title": "哑铃卧推",
          "caption": "3 组 × 10 次 · 休息 90s",
          "tags": ["胸部", "力量"],
          "metadata": { "sets": 3, "reps": 10, "rest_seconds": 90, "muscle_group": "chest" }
        },
        {
          "id": "EXERCISE_003",
          "title": "平板支撑",
          "caption": "3 组 × 45s",
          "tags": ["核心"],
          "metadata": { "sets": 3, "duration_seconds": 45, "muscle_group": "core" }
        }
      ],
      "actions": [
        { "label": "开始", "event": "start_workout",  "params": { "entity_id": "PLAN_001" }, "style": "primary"   },
        { "label": "调整", "event": "adjust_workout", "params": { "entity_id": "PLAN_001" }, "style": "secondary" }
      ]
    }
  ],
  "context_update": {
    "current_entity_id": "PLAN_001",
    "entities": {
      "PLAN_001": {
        "id": "PLAN_001",
        "type": "workout_plan",
        "title": "力量训练",
        "metadata": {
          "duration_minutes": 45,
          "intensity": "medium",
          "exercise_ids": ["EXERCISE_001", "EXERCISE_002", "EXERCISE_003"]
        },
        "created_at": "2026-05-18T09:00:00Z"
      }
    }
  }
}
```

**压测结论：** `display: step_list` 在 HubCard 已有，训练步骤用 `items` 逐条表达，无需新增顶层字段。Skill 专有字段（组数/次数/肌肉群）全进 `metadata`。

---

### A.3 Meeting Skill — 会议摘要 + 异步转录

**触发：** 用户上传录音文件，说"帮我总结刚才的会议"

**特点：** 转录是异步任务（1-3 分钟），先同步返回 TaskStatusCard，完成后推 result_card。

**AgentResponse（同步，创建 task）：**

```json
{
  "skill": "meeting",
  "action": "transcribe_and_summarize",
  "message": "收到录音，正在转录，需要 1-3 分钟。",
  "task": {
    "task_id": "TASK_MTG_001",
    "skill": "meeting",
    "type": "transcribe",
    "status": "queued",
    "message": "排队转录中",
    "poll_url": "/hub/tasks/TASK_MTG_001",
    "poll_interval_ms": 5000
  }
}
```

**TaskCallbackRequest（完成，Skill → Hub）：**

```json
{
  "task_id": "TASK_MTG_001",
  "skill": "meeting",
  "status": "completed",
  "result_card": {
    "id": "CARD_MTG_001",
    "type": "meeting_summary",
    "skill": "meeting",
    "title": "会议摘要",
    "subtitle": "2026-05-18 · 38 分钟",
    "display": "section_list",
    "items": [
      {
        "id": "SECTION_SUMMARY",
        "title": "摘要",
        "caption": "讨论了 AI Hub 协议 v0.1 的 Skill 注册机制与 Card 渲染规范，确认由 Hub 统一管理 context，各 Skill 只返回 context_update。",
        "metadata": { "section_type": "summary" }
      },
      {
        "id": "SECTION_TODO_001",
        "title": "待办：Food Skill 压测示例",
        "caption": "负责人：Devon · 截止：本周五",
        "tags": ["待办"],
        "metadata": { "section_type": "todo", "assignee": "Devon", "due": "2026-05-22" }
      },
      {
        "id": "SECTION_TODO_002",
        "title": "待办：context TTL 策略讨论",
        "caption": "负责人：待定",
        "tags": ["待办"],
        "metadata": { "section_type": "todo", "assignee": null }
      }
    ],
    "actions": [
      { "label": "加入日程", "event": "add_to_calendar", "params": { "entity_id": "MTG_001" }, "style": "primary"   },
      { "label": "查看全文", "event": "view_transcript", "params": { "entity_id": "MTG_001" }, "style": "secondary" }
    ]
  }
}
```

**context_update（随 callback 一起推给 Hub）：**

```json
{
  "current_entity_id": "MTG_001",
  "entities": {
    "MTG_001": {
      "id": "MTG_001",
      "type": "meeting",
      "title": "AI Hub 协议评审",
      "metadata": {
        "duration_minutes": 38,
        "todo_count": 2,
        "transcript_url": "/hub/files/ATT_AUDIO_001.txt"
      },
      "created_at": "2026-05-18T14:00:00Z"
    }
  }
}
```

**压测结论：** `display: section_list` 复用已有枚举；异步转录走标准 task 协议，无需 Meeting 专用异步字段。`section_type` / `assignee` / `due` 全进 `metadata`。

---

### A.4 压测总结

| 验证点 | 结论 |
|---|---|
| AgentResponse 顶层字段 | 三个 Skill 均无需新增，`skill/action/message/cards/task/context_update` 足够 |
| HubCard.display 枚举 | `single/horizontal_carousel/step_list/section_list/grid` 覆盖所有场景 |
| SkillEntity.type | 自由字符串（`outfit/meal/workout_plan/meeting`），Hub 不约束值域 |
| metadata 隔离 | Skill 专有字段（营养素、训练组次、会议待办）全进 `metadata`，通用 Renderer 不读 |
| 异步任务 | Food/Fitness 无需 task；Meeting 异步转录走标准 task 协议，无需新增字段 |
| context_update | 三个 Skill 均用 `entities` 存实体，patch merge 语义一致 |

**结论：协议 core types 对非 Fashion Skill 稳定，v0.1 可作为分工基础。**

---

## 待定事项（下次讨论）

- [ ] Hub 路由 LLM prompt 模板（如何用 Manifest 做 skill 选择）
- [ ] context 过期策略（session TTL；inactive session 清理规则）
- [ ] callback_url 鉴权（token / HMAC signature）
- [ ] 多 Skill 协同场景（如穿搭 + 天气联动）
- [ ] entities key 删除语义（v0.1 不支持，仅支持覆盖/追加）
- [ ] metadata 字段声明机制（每个 Skill 需要在哪里正式声明自己的 metadata schema）
- [ ] 多 Skill 编排（Hub Orchestrator，v0.2）
