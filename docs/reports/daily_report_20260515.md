# 日报 2026-05-15

> 上半为汇报版，下半为附录技术细节。

---

## 一、今日完成

### 1. PRD v1.1 拆解 + DB schema 扩展
基于昨天的会议纪要，确认 v1.0 → v1.1 的 5 项核心变更（多套推荐 / Styling 内联 / OOTD 改卡片 / Looks 不存图 / Color Analysis 推迟）。完成 DB schema 扩展（`image_crop_url / style_tags` + `look` 表 CRUD），解锁 4 条 stream 并行开发。

### 2. Stream A / C 后端全部完成
- **多套推荐 + 拼图生成**：温感动态阈值、风格加权排序、最近 7 天重复惩罚、日级缓存
- **穿搭日志 + 风格档案**：save/get、本月/上月对比、Top5 标签回写
- **意图分类 + 调度层**：Groq LLM + 规则降级，准确率 92%，统一返回 `{action, payload, message}`
- 各模块独立测试覆盖，端到端真实生图验证通过

### 3. Web 端到端跑通（6 个页面 + 21 个 API）
- 首页（hub 化）/ 衣橱 / 日志 / 我的形象 / 随手试穿 / 上传识别页
- **单品上传**：拖图 → Groq Vision 识别 → 用户确认 → 入库 → 后台 image2 美化（异步不阻塞）
- **首页推荐**：横滑卡片 + 风格档案 pill + 快捷入口 2×2 + 底部命令栏（语音/文字指令）
- 衣橱 / 日志 / 用户照片均支持删除
- App / Web 分叉点确认在入口层：`dispatch()` 以下完全复用，未来做 App 只需替换入口

### 4. 随手试穿 + 异步任务层（讨论点 4 方案 C 落地）

PRD §8 的"上传外部衣物图试穿"，按方案 C 实现，把契约设计成将来无缝对接 AI Hub。

**新接口**：
```
POST /api/tasks/quick-tryon (file)  → 立即返回 {task_id}
GET  /api/tasks/{task_id}           → 状态轮询
```

任务对象统一字段（`kind` 预留多任务类型扩展）：
```
{task_id, kind, status: running|done|failed, input_url, result_url, error, created_at, completed_at}
```

**实现要点**：
- in-memory `_tasks` dict 存任务（重启清零，demo 局限）
- `BackgroundTasks` 跑 `_run_quick_tryon` → 调 `phase2_tryon._call_image2_tryon` → 更新 task → 调 `_emit_task_done(task)`
- **`_emit_task_done` 是 hub 集成钩子**，当前 no-op；未来对接时改成 `requests.post(hub_callback_url, json=task)` 即可，**polling/webhook 切换只动这一行**
- 前置检查：无主照 / image2 服务挂了 → 直接报错，不入任务

**前端 `/tryon`**：
- 拖拽 / 点击上传衣物图 → 提交后左右双卡（左：本地预览的衣物图，右：spinner "1-5 分钟"）
- 每 5 秒 poll → done 时右卡换试穿效果图 + `[再试一张] [下载效果图]`
- failed → 红色错误框 + `[重试]`
- 网络抖动 / 503 不放弃，间隔重试

**已知 demo 局限**：
- 用户离开 `/tryon` 后 task_id 丢失（没存 localStorage / URL），服务端任务继续但前端找不回结果
- 多次上传只保留最新 polling，旧任务后台继续但前端看不到
- 上述都是"等 Hub 接入后自动消失"的局限（Hub 推送会替代 polling）

### 5. Stream D 设计稿初稿
7 个页面（AI Hub / 首页 / 内联换装态 / Wardrobe / Looks / Style Identity / OOTD 三步流程），Hub 模式（无 Tab Bar）。

### 6. 首页自动上身（懒触发 + 后台异步生图 + 前端轮询）

PRD §5 明确"首次进入自动生成 4–5 套穿搭效果图"。上午先做的是"点 CTA 才生图"，下午按真实产品形态重写：

- **链路**：进首页 → `/api/recommend` 元数据秒返 + 自动起 BackgroundTask 跑 `generate_outfit_grid` → 返回 `status=generating` → 前端每 8s 轮询 → 命中缓存 → 替换占位
- **状态字段**：`ready / generating / no_photo / image2_offline / no_wardrobe`，前端按 status 切换提示语
- **去掉「生成效果图」CTA**：用户进首页就开始生图，不再要主动点
- **「换一组」按钮** hookup：传 `?refresh=1` 清图缓存 + 重算 + 重生图

### 7. Tomorrow Planning 定时任务（每晚 20:00 预生成）

PRD §5 / §7："设备空闲时（晚 8 点前）预生成明日 4-5 套推荐效果图；每晚 8 点 Hub 推送（图已就绪，零额外等待）"。

- FastAPI `@app.on_event("startup")` 启动 asyncio 后台 loop
- 每天 20:00 触发 `_run_tomorrow_planning`：拿明日天气（wttr.in `weather[1]`）→ `precompute_for_date(明日)` → `generate_outfit_grid` → 写缓存
- `asyncio.to_thread` 包同步 image2 调用，不阻塞事件循环
- 次日首页用「今天」算 cache_key → 命中昨晚预生成 → status=ready 秒开

### 8. 用户文案规范确立

用户反馈"不会入衣橱 / 不入库"这种文案是给开发者看的，UI 上不要出现。删了两处（首页随手试穿入口副标题、`/tryon` hint）。**规则已写入 agent 长期 feedback memory**：用户可见文案只说能得到什么，不说系统不做什么。

### 9. 聊天接入自然语言场合识别

聊天框输入"和朋友去玩穿什么"时，router 正确分类成 recommend，但 dispatch 没把场合信息传下去，导致回复仍是通用 caption。修通后：

- **dispatch → recommender**：`_handle_recommend` 把 `user_input` 作为 `occasion` 透传
- **缓存隔离**：occasion 非空时 `bypass_cache`（不读不写），避免污染首页 hub 的默认缓存
- **LLM caption 真的被调用**：之前 `_make_outfits` 一直调 `_rule_caption`，LLM 函数定义了但没接入。改成首套用 LLM、其他用规则（每次只 1 次 LLM 调用，~500ms）
- **LLM prompt 重写**：system + user 两段式，给具体例子（"轻松又精神，去玩拍照好出片"），强调不要堆形容词、不要重复用户原话、不要解释天气

效果：

| 用户输入 | LLM 回复 |
|---|---|
| 和朋友去玩穿什么 | 休闲又不显懒散，去玩超合适 |
| 今天去见教授穿什么 | 简单不邋遢，见教授挺合适 |
| 出门约会穿什么 | 休闲有型，约会好看！ |

**确认 router 的当前定位**：协议层完整可用（6 类意图分类、92% 准确率），首页底部命令栏即是它的自然语言入口；面向未来设备语音、AI Hub 跨 agent 路由零返工。

### 10. 聊天回复内联单品缩略图

之前聊天回复只有文字 caption，用户问"图呢"。`/chat` 默认 `dry_run=True` 不调 image2 是正确的（image2 1-5 分钟，聊天不能阻塞），但单品图本来就在衣橱里，可以直接贴。

- 后端 `/chat` 加富化：复用 `/api/recommend` 的逻辑给每套 outfit 附 `item_images / item_types`
- 前端 chat 气泡里新增 `.chat-item-row` 70×70 网格，首套单品 4 件秒回展示
- 上方推荐卡片区同步刷新（沿用 `renderOutfits` 已有的 `item_images` fallback 逻辑）

### 11. PWA 半截方案 + iOS LAN 直连

App / Web 分叉点已定在入口层，所以**短期产品形态是 PWA**（设备最终形态再决定要不要纯 native）。今天上半截 PWA：

- **manifest.json**：`name / icons / theme_color / start_url / display:standalone`
- **3 个 icon**：192 / 512 / 180（apple-touch-icon），PIL 脚本生成黑底白 F 占位，Figma 出 logo 直接替换
- **base.html 加 meta**：`apple-mobile-web-app-capable / status-bar-style / theme-color`，`viewport-fit=cover` 解锁 safe-area env 函数
- **safe-area-inset 适配**：`.page-header` padding-top 加 `env(safe-area-inset-top)`（灵动岛/刘海避让）；衣橱/日志弹窗 padding-bottom 加 `env(safe-area-inset-bottom)`（Home Indicator 避让）
- **没做 service worker**：开发期 SW 会缓存导致 hot reload 失效，桌面 web 零影响优先；离线 / 推送等真要做时再上

**LAN 直连**：`uvicorn --host 0.0.0.0`，手机同 WiFi 访问 `http://192.168.31.43:8000` 即可加到主屏。iOS Safari 分享菜单 → 添加到主屏幕 → 全屏 PWA 形态。

### 12. 修复 swap_item：换装真的发生 + 候选空兜底

发现 `_handle_swap_item` 实际**没换任何东西**——把同一份 item_ids 又喂给 image2 重画了一次同样的搭配，还阻塞 1-5 分钟（chat 路径忽略了 dry_run）。

重写后：

- **找替换品**：在当前 outfit 里找目标品类的那件 → 衣橱里同品类、排除当前那件、按 warmth 过滤 → 随机选一件
- **候选 0 时优雅降级**：返回 `available: 0` + 引导文案"暂时只有这件 X，去添加一件吧"，前端展示「去添加单品」按钮跳 `/wardrobe/upload`
- **不调 image2**：chat 秒返新搭配的 item_ids + item_images + swapped_to 元信息；用户想看上身效果再走「自动上身」/ swap_item 后续 CTA
- **聊天回复同 recommend 形态**：4 件单品 2×2 缩略图 + 「保存这套 / 再换一件」按钮

实测当前衣橱（4 上装 + 1 下装）：
- 13°C「换条裤子」→ 候选 0 → 引导上传（正确）
- 13°C「换件上衣」→ 候选 0 → 引导上传（cool 段只准中等/厚，符合预期）
- 22°C「换件上衣」→ 3 候选 → 替换成"蕾丝背心"（正确）

顺带修了相关 UX bug：**聊天回复曾覆盖"今日推荐"卡片区**。已去掉 `renderOutfits` 那一行，聊天回复只在气泡内呈现，首页推荐区保持稳定。

---

## 二、决策与讨论点

### 已决策

| 议题 | 结论 |
|---|---|
| 导航模式 | Hub 模式（无 Tab Bar），子页面顶部 ← 返回首页 |
| AI Look 保存时机 | v1.1 不做，OOTD 统一存单品拼图卡，AI Look 留 v1.2 |
| 用户特征手填入口 | v1.1 砍掉，v1.2 用 Vision 自动获取（skin_tone / body_type 等） |
| 首页生图触发方式 | 懒触发：进首页自动起后台任务 + 前端轮询，不再要用户点 CTA |
| Tomorrow Planning 调度 | FastAPI startup + asyncio loop，每晚 20:00 触发；后续可换 APScheduler / 系统 cron |
| 用户可见文案规范 | 只描述能得到什么，不写"不入库 / 不保留"等开发者解释 |
| 聊天回复形态 | caption + 单品 2×2 缩略图（不调 image2）；要试穿大图走「自动上身」或 swap_item |
| 短期产品形态 | PWA（manifest + safe-area），设备最终形态再决定要不要纯 native；`dispatch()` 协议层不变 |

### 待会议确认

1. **用户全身照上传时机与无照 fallback**
   - 推荐方案：软提示 + 延迟门控（首页常驻软上传 bar，进入需要照片的功能时再轻提示；无照场景用单品 2×2 拼图代替试穿图）
   - 焦点：无照用户的产品价值是否成立？拼图视觉表达够不够？

2. **Tomorrow Planning Hub 推送是否含横滑 outfit 卡片**
   - A：Hub 推送直出横滑卡片，点套进 Fashion Agent 调整
   - B：纯文字 CTA，点进 Fashion Agent 再看图
   - 取决于 Hub 层交互规范

3. **AI Look 保存时机（v1.1 暂不做，v1.2 讨论）**
   - 结论：OOTD 保存统一存单品拼图卡，不弹选项；AI Look 事后从 Looks 页触发
   - 理由：生成阻塞保存流程；弹 3 选项违背零摩擦；双层 timeline 需先验证用户行为和生成质量

4. **随手试穿的异步契约 + 与 AI Hub 的边界**

   背景：当前 `/tryon` 是独立页同步等结果（1-5 分钟卡在页面）。PRD §8 写的是"图发给 AI Hub → 结果以聊天卡形式回 Hub"。**前提澄清**：AI Hub 是设备层级总入口，跨所有 agent；Fashion Agent 是设备上的一个 agent。"结果回 Hub"是跨 agent 设计，不是 Fashion Agent 内部聊天区。

   职责切分：

   | 层 | 责任 |
   |---|---|
   | Fashion Agent | 接请求 → 异步处理 → 完成时发"任务完成"事件，带 result URL |
   | AI Hub | 聚合多 agent 输出 → 决定卡片在 feed 流如何展示 → 路由用户回到对应 agent |
   | 设备 OS | 装载 Hub + 各 agent，负责事件订阅 / 回调地址注册 |

   实现路径：

   | 方案 | 含义 | 工作量 |
   |---|---|---|
   | A. 保持现状 | demo 内同步等，因为 demo 没有 Hub 容器接收异步结果 | 0 |
   | B. mock Hub 层 | Web demo 加 `/hub` 页模拟设备主屏，feed 展示各 agent 卡 | 半天 |
   | **C. 只实现异步契约（推荐）** | 后端做任务/状态接口（`POST /api/tasks/quick-tryon` + `GET /api/tasks/{id}`），demo 内部用 polling 显示；将来对接 Hub 时只需把 polling 换成 webhook | 2-3 小时 |

   **Polling vs Webhook 是同一套契约，只差通知方向**：后端任务流程 / 状态存储 / 结果文件完全一样；polling 是前端拉，webhook 是后端在任务完成时多调一行 `requests.post(hub_callback_url)`。API 字段（task_id / status / result_url）不变，前端代码不动。

   为什么推荐 C：OOTD 识别、Tomorrow Planning 预生成、AI Look 事后生成都是同样的"长任务 + 异步结果"模型，统一一套基建复用率高；当下 demo 用 polling 体验和同步等没区别。

   讨论焦点：
   - v1.1 是否纳入异步任务层？还是当 demo 局限留 v1.2
   - 异步任务表 in-memory 还是入 SQLite？多 agent 场景需要全局任务表吗？
   - AI Hub 回调协议谁来定（硬件平台方 / 我们出建议）？

---

## 三、当前状态

**全链路打通**：上传单品 → Vision 识别 → 入库 + 美化 → 首页自动上身 → 换装/保存 → 查看日志 → 管理我的形象；每晚 20:00 预生成明日推荐。

| 部分 | 状态 |
|---|---|
| 后端核心模块（router / dispatch / recommend / generate / look / style / vision） | ✅ 全部完成 + 测试 |
| Web 6 个页面 + 22 个 API | ✅ 已上线 |
| 自动上身（懒触发 + 异步 + 轮询） | ✅ 已上线 |
| Tomorrow Planning 定时任务（每晚 20:00） | ✅ 已上线 |
| 推荐缓存持久化（JSON 落盘） | ✅ uvicorn reload 不丢 |
| Stream D 设计稿初稿（7 页） | ✅ 完成 |
| 聊天框自然语言场合识别（router + dispatch + 缓存隔离） | ✅ 已上线 |
| 聊天回复内联单品 2×2 缩略图 | ✅ 已上线 |
| PWA + safe-area 适配 | ✅ 已上线（iOS / Android 加主屏全屏） |
| 真实衣橱数据 | ⏳ 待团队 items.json + images/ 交付 |
| Stream B（CLIP 检索 / OOTD 识别） | ⏳ 暂缓，等真实数据 |
| D2 内联换装 | ⏳ 等并行线合并空档期 |

---

## 四、阻塞 / 风险

- **D2 内联换装待启动**：要大量改 `index.html`，与其他并行线（衣橱接入）冲突，需排队
- **CLIP 检索（OOTD 识别）**：需真实衣橱数据到齐才能启动
- **image2 锁人对无脸照效果未知**：用户上传脸被裁/低头的全身照时，新拼图 prompt 能否稳定保持身份待真实样本验证

---

## 五、下一步

1. 等会议确认 2 个待决策点
2. 团队真实 `items.json + images/` 到位 → `import_batch.py` 批量导入 → 验证推荐 + 生图
3. D2 内联换装（合并空档期）
4. CLIP 检索启动（Stream B）
5. 验证 20:00 Tomorrow Planning 第一次实际触发效果（次日观察首页是否命中缓存秒开）

---
---

# 附录 — 技术细节

> 以下为实现要点、bug 修复、trade-off。汇报不需要看，是给自己/技术同事复盘用的沉淀。

## A. 并行开发方法论

DB schema 是唯一前置，完成后 4 条 stream 并行：

```
DB schema 扩展
  ├── Stream A  outfit_recommender → outfit_generator    【生成主链路】
  ├── Stream C  look_manager → style_identity             【日志 + 风格】
  ├── Stream D  App 设计                                   【UI，独立并行】
  └── Stream B  clip_matcher                               【OOTD 识别，暂缓】
```

每模块独立测试入口（`test_*.py`），接口通了再接真实数据，不等其他 stream。

---

## B. 模块清单与测试覆盖

| 模块 | 文件 | 测试 | 说明 |
|---|---|---|---|
| 数据库 | `db.py` | — | 三张表 + Look CRUD + style_tags + image_crop_url + update_user_photo |
| image2 客户端 | `image2_client.py` | — | IP 192.168.31.50，VPN 下可用 |
| 多套推荐 | `outfit_recommender.py` | ✅ 8/8 | 温感阈值，风格加权，重复惩罚，日级缓存，**JSON 持久化**，`precompute_for_date` |
| 拼图生成+裁切 | `outfit_generator.py` | ✅ 5/5 | 一次 image2 调用，PIL 裁切，局部刷新，**prompt 加锁人字段** |
| 穿搭日志 | `look_manager.py` | ✅ 5/5 | save/get，保存后触发风格档案更新 |
| 风格档案 | `style_identity.py` | ✅ 5/5 | 本月/上月对比，归一化分布，Top5 标签 |
| Fashion Router | `fashion_router.py` | ✅ 5/5 | 6 类意图，LLM + 规则降级，准确率 92% |
| Dispatch 层 | `fashion_dispatch.py` | ✅ 6/6 | router → 各模块串联 |
| 端到端联调 | `run_e2e.py` | ✅ 真实生图验证 | 推荐→生图→裁切全链路跑通 |
| Web 入口 | `app.py` (FastAPI) | ✅ smoke | session 内存 context，自动天气，路径转 URL，**BG task / 轮询 / Tomorrow Planning loop** |
| 6 个页面 | `templates/*.html` | ✅ 手测 | 继承 `base.html`，共享设计 token + 组件，首页含底部命令栏 + 自动轮询 |

---

## C. 接口清单（22 个）

```
POST   /chat                          主聊天入口（current_item_ids 可注入；recommend 默认 dry_run=True）
POST   /upload/photo                  上传用户全身照（窄更新，自动设为主照 + clear_cache）
POST   /upload/wardrobe-item          上传单品 → Groq Vision 识别
POST   /confirm/wardrobe-item         确认 → 存 DB + 后台触发 beautify + clear_cache
POST   /api/wardrobe/{id}/beautify    手动触发 beautify（兼容旧单品）
POST   /api/photos/key                切换主照 + clear_cache
POST   /api/tasks/quick-tryon         异步随手试穿（提交，返回 task_id）
GET    /api/tasks/{task_id}           任务状态轮询（done / running / failed + result_url）
GET    /api/wardrobe?category=...     列表 + 过滤
GET    /api/looks?scene=...           日志列表（富化 item_ids → 单品图/类型）
GET    /api/recommend?refresh=&n=     首页自动上身（status + outfits + images，懒触发 BG task）
GET    /api/profile                   当前主照信息
GET    /api/photos                    所有照片 + 主照标记
DELETE /api/wardrobe/{id}             移除单品 + clear_cache
DELETE /api/looks/{id}                删除日志
DELETE /api/photos/{filename}         删除照片（删主照自动晋升另一张 + clear_cache）
GET    /                              首页（卡片 + 命令栏）
GET    /wardrobe                      衣橱页
GET    /wardrobe/upload               上传页
GET    /looks                         日志页
GET    /profile                       我的形象页
GET    /tryon                         随手试穿页
```

---

## D. 数据流

**聊天链路**：

```
用户输入
  → POST /chat（携 current_item_ids）
  → fashion_dispatch.dispatch(dry_run=True)   recommend 不生图
  → fashion_router.route()                    Groq llama-3.3-70b
  → outfit_recommender / outfit_generator / look_manager / db
  → (swap_item) image2_client.generate()      仅 swap_item 走生图
  → 返回 {action, payload, message}
  → 前端按 action 渲染 inline 卡片
```

**自动上身链路**：

```
进首页
  → GET /api/recommend
  → outfit_recommender.recommend_outfits()   元数据秒返
  → 缓存无图 + 有主照 + image2 在线
      → BackgroundTask: outfit_generator.generate_outfit_grid()
                          ↳ image2 拼图 → PIL 裁切 → set_cached_images() → _persist()
  → 返回 status=generating
前端每 8s 轮询 GET /api/recommend
  → 命中 image_cache → status=ready + images → 替换占位
```

**Tomorrow Planning 链路**：

```
FastAPI startup
  → asyncio.create_task(_tomorrow_planning_loop)
      → sleep_until(20:00) → asyncio.to_thread(_run_tomorrow_planning)
          → _fetch_weather_for_day(1)                  wttr.in weather[1]
          → precompute_for_date(default, weather, tomorrow_date, 4)
          → generate_outfit_grid → set_cached_images(明日 key)
次日首页 GET /api/recommend
  → cache_key=(default, today, temp_bucket)            今天=昨天预生成时的明天
  → 命中 image_cache → status=ready 秒开
```

**单品上传链路**：

```
拖图上传
  → POST /upload/wardrobe-item
  → phase1_wardrobe.recognize_clothing()                Groq llama-4-scout vision
  → 前端可编辑确认（含 placeholder 文字占位）
  → POST /confirm/wardrobe-item
  → 立即返回 (存原图，UI 显示"美化中" badge) + outfit_recommender.clear_cache()
  → BackgroundTasks: image2 beautify (1-5 min)
  → 更新 DB image_url
  → 前端 8s 轮询 → badge 消失 → 图自动替换
```

---

## E. 关键实现要点

### E.1 Beautify 异步设计
image2 单次调用 1-5 分钟（含连接重试），不能阻塞 HTTP。`BackgroundTasks` 立即返回 → 后台任务跑 image2 → 完成后更新 DB；`_beautifying` 内存集合标记进行中，前端轮询 + badge 反馈。image2 不可达时跳过 beautify 但仍入库，不影响主流程。

### E.2 推荐打分
- 风格档案衰减权重 `1.0 / 0.8 / 0.6 / 0.4 / 0.2`
- 最近 7 天 looks 重叠率 ≥50% 扣分（`-overlap`）
- 微随机扰动（`+random()*0.1`）防止同分死锁
- 日级缓存 key 含温度分桶（cold ≤5 / cool ≤15 / warm ≤25 / hot），衣橱变动钩子自动 invalidate

### E.3 Vision 主体收敛
原 prompt"识别所有衣服…不允许省略"跟"一次一件主体"的实际场景反了。重写后加入：
- 三条主体判断规则（占面积/居中/清晰、被刻意展示、同款不同色算多件）
- 必须忽略清单（背景货架、模糊遮挡、镜子反射、模特身上）
- 兜底："宁可漏识别，不要把背景当主体；没有主体返回空数组"

实测：商场粉色背心展示图，识别从 6 件（含 5 件背景鞋）→ 1 件（仅主体）。

### E.4 App / Web 分叉
`dispatch()` 是协议层，返回 `{action, payload, message}`。Web 用 FastAPI 包一层 HTTP，App 未来直接调或包 native HTTP client，**dispatch 以下零改动**。

### E.5 自动上身（懒触发链路）
- `outfit_recommender` 加 `_IMAGE_CACHE` + `_GENERATING` 两个状态结构；公开接口 `cache_key / get_cached_images / set_cached_images / is_generating / mark_generating`
- `app._generate_outfit_images_bg(key, user_photo, outfits)` 作为 BG task 函数，跑完写图缓存并清 `_GENERATING` 标记
- `/api/recommend` 返回 `status` 字段，5 种状态对应不同前端 UI；命中已生成图直接 `ready` + images，否则起 BG task 并返回 `generating`
- 防重复触发：`mark_generating(key, True)` 在 BG task 提交前置位，跑完置否
- 前端 `loadRecommend()` 检测 `status === "generating"` 时 `setTimeout(8000)` 轮询

### E.6 Tomorrow Planning 定时调度
- 不依赖 APScheduler / cron，用 asyncio 内置：`@app.on_event("startup")` 起 loop，`while True: await asyncio.sleep(_seconds_until_next(20))` → `await asyncio.to_thread(_run_tomorrow_planning)`
- `_run_tomorrow_planning` 是同步函数（包含 image2 阻塞调用），`to_thread` 扔线程池跑，不阻塞事件循环
- 明日 cache_key 用「明天」的 date_iso 写入；次日自然变成「今天」，`/api/recommend` 用 `date.today()` 算 key → 命中
- 失败容错：image2 不在线 / 无主照 / 衣橱空 都直接 print 跳过，不影响 loop 继续

### E.7 推荐缓存 JSON 持久化
**触发**：发现 BG task 跑完写盘了 `images/grid/*.png`，但首页 status 永远 generating。
**根因**：`_IMAGE_CACHE` 是内存 dict，`uvicorn --reload` 每次代码改重启 → 内存清零 → 旧 BG task 产物在盘上但被弃用 → 新进程进来又触发新一轮。
**修法**：`images/recommend_cache.json` 持久化 `_CACHE` + `_IMAGE_CACHE`。模块加载 `_load()` 时验证图文件还在再写入内存。`set_cached_images / clear_cache / recommend_outfits / precompute_for_date` 写入后调 `_persist()`。`_GENERATING` 不持久化（重启清零，新进程视作未生图重新触发即可）。

### E.9 自然语言场合识别 + caption 双模

**链路**：聊天 `/chat` → `dispatch._handle_recommend(user_input)` → `outfit_recommender.recommend_outfits(occasion=user_input)` → `_make_outfits` 首套调 `_llm_caption`、其余 `_rule_caption` → 返回。

**缓存隔离**：`occasion` 非空时 `bypass_cache = True`（不读不写 `_CACHE`），避免污染首页 hub 的默认缓存（hub 用空 occasion 路径，命中常规缓存）。

**首套 LLM、其余规则**的取舍：image2 之外 Groq LLM 单次 caption 约 500ms-1s，全部 LLM 化 6 套要 3-6 秒。首套是用户**实际看到的那套**（chat message + 卡片第一张 + 缩略图），用 LLM 保自然口语；后面 5 套作为「换一组」/「左右滑」备选，用规则保延迟。

**LLM caption prompt 关键**：
- system 写明角色（"穿搭助手"）+ 输出要求（25 字内、朋友式口语、不堆形容词、不重复用户原话、不解释天气）+ 给两个示例（"轻松又精神，去玩拍照好出片"、"利落不松垮，见导师不显凶"）
- user 只放结构化字段：`场景 / 天气 / 搭配（含品类 + 风格）`
- 返回后 `strip("「」\"'.")` 去掉模型偶尔加的引号 / 句号

### E.10 拼图 prompt 锁人
**触发**：女用户单图试穿正常，首页拼图被画成男的。
**根因**：`outfit_generator.regenerate_single_outfit` prompt 有"保持用户脸部和体型不变"锁人；`grid_v1.json` 模板只描述上身效果 + 品类词，image2 看到"背心"等品类自由发挥。
**修法**：grid_v1.json 改为「请基于参考图中的用户全身照…严格保持参考图中用户的脸部、发型、体型、性别特征不变，仅替换服装。四格中是同一个人。」不显式塞「女性」（PRD §4 不做性别问卷），让 image2 从参考图自己推断。`_load_config` 每次 generate 重读 json，新 prompt 立即生效。

### E.11 PWA + safe-area
- **半截 PWA**（不做 SW）：iOS / Android 长按"添加到主屏幕"即可全屏运行，桌面 web 零影响。SW 在 dev 期间容易缓存住旧 CSS，且 LAN HTTP 不支持 SW（要 HTTPS），暂不需要。
- **icons 用 PIL 生成**（黑底白 F 占位）：要换正式 logo 只动 `static/icon-*.png` 三个文件，代码不动。
- **safe-area 是 iOS PWA / 全屏 web 必备**：`viewport-fit=cover` 解锁 `env(safe-area-inset-*)` → header `padding-top` 加 top inset，弹窗 `padding-bottom` 加 bottom inset。非 iPhone 设备 fallback 为 0，桌面 web 无变化。这一块不是设计偏好是结构性适配，Stream D 设计稿出来重做样式也保留。

---

## F. Bug 修复 / 调试踩坑

| 问题 | 根因 | 修复 |
|---|---|---|
| 前端天气栏溢出整个页面 | `wttr.in/?format=%t+%C` 偶发返回完整 HTML 页 | 改 JSON API `wttr.in/?format=j1` + header `overflow:hidden` |
| 推荐图全部 404 | `_to_url()` fallback 取 `p.name`，丢了 `grid/` 子目录 | 区分绝对/相对路径，相对路径前直接加 `/` |
| Groq Vision 报 `model_decommissioned` | `llama-3.2-11b-vision-preview` 已下线 | 换 `meta-llama/llama-4-scout-17b-16e-instruct` |
| 单品上传后未美化 | Beautify 代码加入前上传的单品没机会触发 | 新增 `POST /api/wardrobe/{id}/beautify` + 弹窗"重新美化"按钮 |
| 详情图被裁切 | `.modal-img` 用了 `object-fit: cover` | 改 `contain` + `max-height: 420px` |
| user_profile 上传必崩 | `KeyError: 'upload_time'` 漏字段 | 加 `update_user_photo()` 窄更新 |
| user_profile 上传抹数据 | `upsert_user_profile` 是全字段 UPDATE，端点传 `""` 覆盖已有值 | 同上，窄更新只动 photo_url + upload_time |
| 聊天页布局空白 | `.page-content` 缺 `flex-direction: column`，子元素 flex:1 不生效 | index.html `extra_style` 覆盖 |
| 首页对话功能被改 hub 化后丢失 | PRD §5 要求语音/文字输入也在首页响应，重构时漏了 | index.html 加底部 sticky 命令栏 + inline `agent-response` 卡，复用 `/chat` |
| 首页发指令 swap_item 失败 | session 无 `current_item_ids`（用户进首页是 `GET /api/recommend`，不经过 /chat） | `ChatRequest` 加可选 `current_item_ids` 字段，前端发指令时把当前展示的 outfit 带上 |
| `grid/` 目录积累 13 MB 拼图 | `fashion_dispatch._handle_recommend` 默认 `dry_run=False`，每次 `/chat`「今天穿什么」都跑 image2 | `app.chat()` 调 dispatch 时显式传 `dry_run=True`，生图入口统一到自动上身 + swap_item |
| 主照换了拼图还是旧脸 | cache_key 不含 photo 维度 | 3 个写主照入口（`/upload/photo` / `/api/photos/key` / DELETE 主照）后都调 `outfit_recommender.clear_cache()` |
| 5 分钟过了还看不到上身图 | `_IMAGE_CACHE` 是内存 dict，uvicorn `--reload` 每次代码改重启清零，旧 BG task 产物在盘上但被弃用 | 见 E.7（JSON 持久化） |
| 拼图把女用户画成男的 | grid_v1.json prompt 没锁人物特征 | 见 E.10（prompt 加"严格保持脸部、发型、体型、性别特征不变"） |
| 聊天 caption 一直是规则模板 | `_make_outfits` 写死调 `_rule_caption`，LLM 函数定义了但没接 | 首套改调 `_llm_caption`（自带 fallback 到规则）；其余仍走规则保延迟 |
| 聊天回 caption 包含"穿什么"疑问词 | 规则 caption 用 `f"适合{occasion}"` 把整句原文拼进去 | LLM caption prompt 重写让模型自然消化场景，不再字面拼接 |
| 聊天没回单品图 | `/chat` 返回的 outfits 只有 item_ids，前端无法渲染 | `/chat` 富化 outfits 加 `item_images / item_types`（复用 `/api/recommend` 逻辑）；前端 chat 气泡内联 2×2 缩略图 |
| 用户两次问不同场合，第二次拿到第一次的缓存 caption | `_CACHE` key 只含 `(user_id, date, temp_bucket)`，不含 occasion | occasion 非空时 `bypass_cache`（既不读也不写） |
| 顶部 header 和 iPhone 灵动岛重合 | base.html `.page-header` 没用 safe-area | 加 `padding-top: env(safe-area-inset-top)` + `height` 同步加 inset |
| swap_item 没真换 + 阻塞 1-5 分钟 | `_handle_swap_item` 把同 item_ids 喂回 image2；chat 路径忽略 dry_run | 重写：找替换品 + warmth 过滤 + 候选空兜底；不调 image2，秒返 item_images 缩略图 |
| 聊天回复覆盖"今日推荐"卡片区 | `handleAgentResponse` 收到 recommend 时调 `renderOutfits` 把首页推荐区刷成场合推荐 | 去掉这行；聊天临时回复只在气泡内呈现 |

---

## G. Trade-off / 已知问题

| 问题 | 状态 / 备注 |
|---|---|
| 「换一组」按钮当前 no-op | ✅ 已解：前端 `loadRecommend(true)` 调 `?refresh=1` |
| 缓存只在内存 | ✅ 已解：JSON 持久化到 `images/recommend_cache.json` |
| Tomorrow Planning 待排期 | ✅ 已解：asyncio loop + startup event 已上 |
| 风格 cold start | 用户没保存过 look → `style_tags` 空 → 加权失效退化随机；预期行为，符合零摩擦 |
| Vision 模型边缘 case | 改 prompt 已大幅收敛；如仍有漏切，备选方案是客户端裁切组件（上传前用户框选主体） |
| `_GENERATING` 标记不持久化 | 进程重启时"正在生图但被杀"的任务，新进程会重新触发——可能浪费一次 image2 调用，但产品行为正常 |
| Tomorrow Planning image2 离线无重试 | 当前直接 print 跳过；可加：30 分钟后再试一次，仍失败放弃 |
| image2 锁人对无脸照效果未知 | 用户上传脸被裁/低头的全身照时，新拼图 prompt 能否稳定保持身份待真实样本验证 |
| LLM caption 只给首套 | 性能折衷（全套 LLM 要 3-6s）；用户实际只看首套（chat / 卡片首张 / 缩略图），其余规则可接受 |
| PWA 没接 service worker | LAN HTTP 不支持 SW（要 HTTPS）；离线访问 / 推送等真要做时再上 |
| 多维度抽取 / 并行查询多源（multi-aspect extraction + tool fanout） | v2 方向：扩 router 输出结构化 `{intent, occasion, time, style_hint, activity, ...}` + dispatch 里 `asyncio.gather` 并行查多源（衣橱/历史 looks/风格/天气/日历），等真实衣橱数据 + 行为日志攒起来收益最高 |

---

## H. v1.2 ToDo（用户特征自动获取，替代已删 CLI 手填）

| 字段 | v1.2 计划来源 | v1.1 现状 |
|---|---|---|
| `skin_tone` / 色季 | 主照 → Vision 模型 | 空 |
| `body_type` | 主照 → Vision 模型（身型轮廓） | 空 |
| `height` | 用户自选（或 Vision 估算） | 空 |
| `style_preference` | Agent memory 从 look + 对话积累 | 空 |
| `temp_offset` | Agent memory 从冷热反馈积累 | 现有逻辑保留 |
