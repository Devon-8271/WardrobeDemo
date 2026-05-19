# 日报 2026-05-19

> 上半为汇报版（manager 看），下半为附录技术细节（自己/技术同事看，非必读）。

---

## 一、今日完成

1. **推荐引擎重构为两阶段召回**（`outfit_recommender.py`）
   - 原逻辑：全量笛卡尔积 → 打分排序，衣橱大时组合数爆炸（80 上装 × 60 下装 × 10 外套 × 5 鞋 = 240,000 组合）
   - 新逻辑：Stage 1 硬过滤冷暖 → 风格排序 → 每 slot 取 top-K（上/下装 K=10，外套 K=3，鞋 K=3）；Stage 2 只在 top-K 子集内组合，最坏情况 900 组合，上限恒定
   - 修复外套/鞋始终取第一件的 bug：现在外套 top-3、鞋 top-3 均参与排列组合，推荐多样性提升
   - Caption 由"只对第一套调 LLM，其余用规则模板"升级为**全套并发 LLM**（ThreadPoolExecutor，4 并发，总耗时与原来相当）

2. **试穿去重缓存，避免重复调用 image2**
   - **Look 试穿**（`look_manager.py`）：`_generate_tryon_bg` 生成前先扫描历史 looks，相同单品组合（`frozenset` 比对）已有 `tryon_url` 则直接复用，跳过 image2
   - **Quick tryon**（`app.py`）：新增 `_TRYON_CACHE`（内存 + `images/tryon_cache.json` 持久化），key = `md5(用户照) + md5(衣物图)`，命中后直接返回缓存路径，不起 image2 任务

3. **封装 `tryon_skill.py`**（新模块，A + B = C 试穿 Skill）
   - 统一接口：`run(person_photo, item_images, items, ...)` 单件/套装均适用；`run_grid(user_photo, outfits, wardrobe, cols, rows)` 一次 image2 调用生成拼图
   - 内部自动：`pose_engine.build_pose_hint`（OOTD 全套上下文）+ 场景自选（室内/户外/棚拍）+ prompt 构建
   - 所有上身图场景统一走 skill，不再各自拼 prompt 直调 image2

2. **推荐图 / 试穿 / 单套重新生图 全部收口到 `tryon_skill`**
   - `outfit_generator.generate_outfit_grid()` → `tryon_skill.run_grid()`（含每格独立 pose_hint）
   - `outfit_generator.regenerate_single_outfit()` → `tryon_skill.run()`
   - `app._run_tryon_outfit()` → `tryon_skill.run()`（套装试穿 BG task）
   - `app._run_quick_tryon()` → `tryon_skill.run(items=[])`（随手试穿，无 metadata）
   - `phase2_tryon.virtual_tryon()` → `tryon_skill.run()`（CLI 单件试穿）
   - 删除 `_QUICK_TRYON_PROMPT`、`_build_tryon_prompt`、`_call_image2_tryon`、`_build_grid_prompt`、`_collect_images` 等分散的 prompt 碎片

3. **试穿接入直接 REST 入口**（`POST /api/tasks/tryon-outfit`）
   - Hub 尚未搭建，暂以直接端点代替 Hub Action 路由
   - 接受 `{item_ids}`，前置校验全身照 + image2 在线，起 BG task，返回 `task_id`
   - 前端轮询 `GET /api/tasks/{task_id}` 取结果

4. **上传 → 试穿预览 → 决策链路**（`POST /api/tasks/preview-tryon` + `wardrobe_upload.html`）
   - 「添加单品」流程加「上身预览」：Groq Vision 识别后，点预览 → 后台 `tryon_skill.run()` 生成上身效果图 → 用户看效果再决定是否入库
   - 无需重新上传：`preview-tryon` 接受已上传的 `image_path` + 识别元数据，不走两遍 multipart
   - 前端：初始显示 [上身预览] [存入衣橱]，效果图出来后切换为 [加入衣橱] [不要了]；「不要了」灰化卡片并标注「已跳过」

5. **首页后台任务通知 — 小红点**（`index.html` + `wardrobe_upload.html`）
   - 核心体验点：预览图后台生成（1-2 min），用户可随时离开，首页等待提示
   - 实现：提交后 `task_id` 写入 `localStorage.wdb_preview_tasks`；首页全局 poller 每 10s 轮询一次，完成后「添加单品」入口卡片右上角亮 **红点**，副标题变「N 件试穿结果待查看」
   - 用户点进去 → 红点消失、副标题复原；若用户留在上传页等到结果，页面直接展示，同时从 pending 移除，不打首页红点

---

## 二、决策与讨论点

### 已决策

| # | 议题 | 最终决定 |
|---|---|---|
| 1 | 2000 件衣物打标方案 | 弃用 Groq Vision 直调，改用同事 MCP server（`submit_outfit_photo` → `get_outfit_items` → `get_item_image` + `extract_item_tags`），同时获得白底图 + 结构化标签 |
| 2 | 图片上传方式 | 本地文件夹 → POST `http://192.168.31.113:9000/upload` → 拿 LAN URL → 交给 MCP pipeline；注意 localhost 替换为 LAN IP |
| 3 | 博主数字衣橱 Demo 时间节点 | 博主明天（2026-05-20）来公司；需今晚完成接口验证并起批跑，明早跑完 2000 张入库 |

### 待确认

1. **`/upload` 接口格式** — multipart form（`file` 字段）还是其他？等同事确认后才能写 upload 步骤

---

## 三、当前状态

| 模块 | 状态 |
|---|---|
| MCP server 工具理解（4 个工具链路） | ✅ 今日梳理 |
| `tryon_skill.py` 封装（run + run_grid + pose_engine 集成） | ✅ 完成 |
| 所有上身图场景收口到 tryon_skill | ✅ 完成 |
| 试穿直接 REST 入口（`POST /api/tasks/tryon-outfit`） | ✅ 完成 |
| 上传 → 试穿预览 → 决策链路 | ✅ 完成 |
| 首页后台任务通知（添加单品小红点） | ✅ 完成 |
| `mcp_label.py` 编写 | ✅ 用户自写完成；MCP server 需起动 |
| 2000 张图片批量跑通（入库） | ❌ 待 MCP server 启动后起跑 |
| 博主 Demo 环境就绪 | ❌ 需批跑完成 + import_batch.py 入库 |

---

## 四、阻塞 / 风险

- **MCP server 未启动**：`mcp_label.py` 已写好，但 `192.168.31.113:9001` 连接被拒；需同事启动 MCP server 才能起批
- **时间压力**：2000 张 × ~15-20s/张 ÷ 3 并发 ≈ 2-3 小时；今晚必须起跑才能明早跑完
- **MCP 响应格式未实测**：`extract_item_tags` 字段结构在实际数据上未验证，起跑前先跑 `--limit 3` 确认格式

---

## 五、下一步

1. **等同事启动 MCP server**（`192.168.31.113:9001`）
2. **小批验证**：`python mcp_label.py /Volumes/home/数据采集 --workers 1 --limit 3`，确认 items.json 格式正确
3. **今晚起批**：`python mcp_label.py /Volumes/home/数据采集 --workers 3`，后台跑 2000 张
4. **明早入库**：`python import_batch.py items.json`
5. **博主 Demo 环境检查**：wardrobe DB 数据正常、Web 前端推荐页展示正常

---
---

# 附录 — 技术细节

> 以下为实现要点与设计细节，汇报不需要看。

## A. 推荐引擎两阶段召回设计

### A.1 复杂度对比

| | 原逻辑 | 新逻辑 |
|---|---|---|
| 组合数 | O(tops × bottoms × outers × shoes) | 恒定 ≤ K_top² × K_outer × K_shoe = 900 |
| 外套/鞋参与方式 | 全部取 `[0]` | top-K 参与排列组合 |
| Caption | 第 1 套 LLM，其余规则 | 全套并发 LLM（4 并发） |

### A.2 `_retrieve_slot` 双层策略

```
硬过滤（warmth in allowed_warmth）
  → 不足 K 时放宽（fallback_ids 记录，warmth_warning=True）
  → 按 style_weight 排序
  → 返回 top-K
```

### A.3 试穿去重策略

```
Look tryon：frozenset(item_ids) 命中历史 tryon_url → 写回 DB，return
Quick tryon：md5(user_photo) + md5(item_path) → tryon_cache.json → 跳过 image2
```

---

## B. tryon_skill 架构设计

### A.1 接口一览

```python
tryon_skill.run(person_photo, item_images, items=[], fit_hint="", styling_hint="", color_override="")
  → str  # 单张试穿效果图路径

tryon_skill.run_grid(user_photo, outfits, wardrobe=None, cols=2, rows=2)
  → str  # 拼图路径（调用方裁切）
```

### A.2 所有上身图调用路径

```
推荐图（4套）    outfit_generator.generate_outfit_grid()  → tryon_skill.run_grid()
单套重新生图     outfit_generator.regenerate_single_outfit() → tryon_skill.run()
套装试穿按钮     app._run_tryon_outfit()                  → tryon_skill.run()
随手试穿         app._run_quick_tryon()                   → tryon_skill.run(items=[])
CLI 单件试穿     phase2_tryon.virtual_tryon()             → tryon_skill.run()
```

### A.3 run_grid pose 注入方式

- 对每套 outfit 独立调 `pose_engine.build_pose_hint(items[0], ootd_items=items)`
- prompt 里每格单独写姿势段：`【第N格】T恤、阔腿裤  颜色：…\n  姿势：慵懒街头站姿…`
- 背景：从所有 outfit 的 style 标签里多数派决定（室外 / 室内 / 棚拍）

### A.4 关键决策

| 决策 | 理由 |
|---|---|
| Grid 保留一次 image2 调用 | 4 次单独调用慢 4 倍，grid 裁切后分辨率够用 |
| OOTD 全套传 pose_engine | 单品标签易误判风格；完整套装上下文才能准确判 power/soft/formal |
| image_paths 限 8 张 | image2 服务上限；超限时优先按 outfit 顺序截取 |
| Hub Action 暂用直接 REST | Hub 尚未搭建，`POST /api/tasks/tryon-outfit` 作为临时入口 |

---

## B. mcp_label.py Pipeline 设计

### B.1 完整流程（2 步）

```
本地图片目录（/Volumes/home/数据采集/...）
  │
  ▼ to_nas_url()  →  nas://数据采集/帽子/x.jpg
  │
  ▼ Step 1  generate_item_image(nas://...)   → 白底图 URL
  │
  ▼ Step 2  extract_item_tags(白底图 URL)   → 结构化标签 fields
  │
  ▼ 输出 items.json（兼容 import_batch.py）
```

MCP server 与 NAS 同机，直接接受 `nas://` 路径，无需先 POST 上传。

### B.2 工程设计

| 功能 | 实现 |
|---|---|
| 断点续跑 | `mcp_label_progress.json`，key = nas:// URL |
| 失败分流 | `mcp_label_skip.json`，任意步骤失败写入 |
| 并发 | `ThreadPoolExecutor(workers=3)`，图片级并发 |
| 品类兜底 | `FOLDER_CATEGORY` 字典，`extract_item_tags` 未返回 category 时按目录名映射 |
| 输入格式 | 本地目录（递归扫 jpg/png/webp）|

### B.3 时间估算（2000 张）

| 参数 | 值 |
|---|---|
| 每张平均耗时 | ~15-20s（含 generate_item_image） |
| 并发数 | 3 |
| 预计总时长 | 2-3 小时 |
| 最晚起跑时间 | 今晚 21:00（保证明早 8:00 前跑完） |

---

## C. 首页后台任务通知设计

### C.1 数据流

```
wardrobe_upload.html
  previewTryon() 提交成功
    → localStorage.wdb_preview_tasks.push({ task_id, item_label })

index.html（全局 poller，每 10s）
  for each pending task:
    GET /api/tasks/{task_id}
    completed → wdb_preview_results.push({ task_id, result_url, item_label })
               localStorage.wdb_preview_tasks 移除该条
               #upload-badge.show / 副标题 = "N 件试穿结果待查看"
    failed    → 直接丢弃（不打点，不报错）
    pending   → 保留，下轮继续查

用户点「添加单品」
  → localStorage.wdb_preview_results 清空
  → badge 消失，副标题复原
```

### C.2 边界情况

| 情况 | 处理 |
|---|---|
| 用户留在上传页等到完成 | 页面直接渲染效果图，同时从 `wdb_preview_tasks` 移除；首页不打点 |
| 用户多次提交预览 | `wdb_preview_tasks` 是数组，poller 逐条查；results 累加，badge 数字递增 |
| image2 离线 / 任务失败 | 失败任务从 pending 丢弃，不打首页红点 |
| 页面关闭再打开 | localStorage 持久化，poller 在 DOMContentLoaded 后重新启动，继续轮询 |
