# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI 硬件穿搭助手 — 面向摄像头 + 麦克风 + GPS 设备。核心功能：衣橱建模、虚拟试穿、穿搭推荐。

两个工作目录：
- `demo/` — 原型版，含完整 CLI 流程（main.py + phase1/2/3 + router + prompt_builder）
- `5.14MVP/` — **当前主线**，FastAPI Web 服务，含衣橱管理、穿搭推荐、试穿、穿搭日志

## Commands

```bash
# 安装依赖（在 demo/ 或 5.14MVP/ 下各执行一次）
pip install -r requirements.txt

# 启动 Web 服务（主线）
cd 5.14MVP && uvicorn app:app --reload --port 8000 --host 0.0.0.0
# 局域网访问地址：http://192.168.31.43:8000

# 运行 demo 完整 CLI
cd demo && python main.py

# 批量导入衣物数据
cd 5.14MVP && python import_batch.py <items.json 路径>

# 快速测试试穿（不需要 DB）
cd 5.14MVP && python test_tryon.py <全身照> <衣物图> [衣物描述]

# 运行单个测试（所有测试都是独立 Python 脚本，直接执行）
cd 5.14MVP && python test_fashion_router.py
cd 5.14MVP && python test_recommender.py
cd 5.14MVP && python test_outfit_generator.py
cd 5.14MVP && python test_fashion_dispatch.py
```

## Architecture（5.14MVP 主线）

### 请求流

```
HTTP → app.py（FastAPI）
         │
         ├── /chat           → fashion_dispatch.dispatch()
         │                        └── fashion_router.route()（Groq LLM + 规则兜底）
         │                             ├── recommend  → outfit_recommender → outfit_generator（image2）
         │                             ├── swap_item  → db + outfit_recommender
         │                             ├── wardrobe_query → db
         │                             └── save_look  → look_manager → style_identity（静默更新）
         │
         ├── /upload/wardrobe-item → phase1_wardrobe.recognize_clothing（Groq Vision）
         ├── /confirm/wardrobe-item → db.insert + 后台 beautify（image2）
         ├── /api/tasks/quick-tryon → phase2_tryon（image2，轮询 /api/tasks/{id}）
         └── /api/recommend  → outfit_recommender + outfit_generator（懒触发后台生图）
```

### 模块职责

| 模块 | 职责 |
|---|---|
| `app.py` | FastAPI 入口；所有 HTTP 路由；会话管理（内存）；每晚 20:00 Tomorrow Planning 定时任务 |
| `db.py` | SQLite CRUD — `wardrobe` / `user_profile` / `look` 三张表 |
| `fashion_router.py` | 意图分类：Groq `llama-3.3-70b-versatile` → 规则兜底；返回 `recommend/swap_item/quick_tryon/wardrobe_query/save_look/unknown` |
| `fashion_dispatch.py` | 调度层：接收 route key → 调对应处理函数 → 返回 Hub Action 标准 `AgentResponse`（含 cards/context_update/entities） |
| `outfit_recommender.py` | 按天气/场合推荐 N 套搭配；内存+磁盘双层缓存（`images/recommend_cache.json`）；用户温感偏移 |
| `outfit_generator.py` | 轻量编排层：委托 `tryon_skill.run_grid()` 生成拼图 → PIL 裁切为单张效果图 |
| `tryon_skill.py` | **A+B→C 试穿核心引擎**。构建 image2 prompt，集成 pose_engine + scene_engine 自动选姿势和背景。提供 `run()`（单套）和 `run_grid()`（多套拼图）两个入口 |
| `pose_engine.py` | 时尚动作原子库：场景决定动作锚点（走路/坐/镜自拍等），风格决定气质修饰（power/soft/formal），品类决定构图约束（全身/鞋履/包等必须展示什么） |
| `scene_engine.py` | 背景场景选择器：四层决策（occasion → 单品 style 强地点词 → mood×weekend 兜底 → 默认 commute_street），每个场景组下有 3-6 个 variant（含 prompt + occasion/best_for 标签），两级打分选最优 |
| `phase1_wardrobe.py` | Groq Vision（llama-4-scout）识别单品 + image2 美化平铺图 |
| `phase2_tryon.py` | 调 image2 做虚拟试穿（quick_tryon 任务） |
| `look_manager.py` | 保存穿搭日志；保存后静默触发 `style_identity` 更新 |
| `style_identity.py` | 从 look 历史推断用户风格档案，写回 `user_profile.style_tags` |
| `image2_client.py` | image2 图像生成客户端：submit → poll → download，封装健康检查 `healthz()` |
| `import_batch.py` | 批量导入 items.json，含字段校验和图片复制 |

### DB Schema

`wardrobe`：`item_id, category, type, raw_type, color*, style*, season*, warmth, fit, description, image_url, image_crop_url, source, upload_time`

`user_profile`：`user_id, photo_url, height, body_type, skin_tone, style_preference*, temp_offset, personal_color, style_tags*, last_outfit_date, upload_time`

`look`：`look_id, date, item_ids*, photo_url, scene, source, user_id`

\* JSON 字符串，读取时 `json.loads` 反序列化。

### 推荐缓存机制

`outfit_recommender` 用 `(user_id, date_iso, temp_bucket)` 作为 cache key，分两层：
- `_CACHE`：推荐组合元数据（内存 + `images/recommend_cache.json`）
- `_IMAGE_CACHE`：image2 生成的拼图路径（内存 + 同文件）

衣橱变动（上传/删除）或用户换主照时调 `outfit_recommender.clear_cache()` 使缓存失效。

## 试穿 Prompt 构建流程（v2）

```
outfit_generator.generate_outfit_grid()
  └── tryon_skill.run_grid()                    # 编排器
        ├── scene_engine.pick_scene_group()      # 决定场景组（commute_street/date_restaurant/...）
        │     ├── occasion 子串匹配 _SCENE_HINT
        │     ├── 单品 style 命中 _STRONG_LOCATION_HINTS 白名单时触发场景
        │     └── 兜底：mood × weekend → _STYLE_TO_DEFAULT_SCENE
        ├── scene_engine.pick_scene_with_variant() # 场景组内选具体 variant（含 prompt + id，grid 四格去重）
        ├── pose_engine.build_pose_hint()        # 姿势建议：action anchor + mood modifier + composition rules
        └── _build_grid_prompt()                 # 拼装最终 image2 prompt

tryon_skill.run()                                # 单套试穿入口（phase2_tryon / outfit_generator.regenerate 复用）
  └── 同上，但只生成一套 prompt
```

**场景组一览**：`commute_street`（通勤街拍）、`office_clean`（办公室极简）、`date_restaurant`（约会/餐厅）、`travel_vacation`（旅行/度假）、`weekend_market`（周末市集）、`party_night`（派对/夜生活）、`campus_casual`（校园）、`home_mirror`（镜自拍）

**风格 mood**：单品 style 标签自动归入 `power`（街头/机能/高街）、`soft`（法式/度假/学院）、`formal`（通勤/极简/老钱），影响场景选择和动作气质。

## Key Constraints


**image2 服务**：本地图像生成，需与设备在同一 Wi-Fi（`192.168.31.50:8787`）。生成耗时 1-5 分钟，调用前先 `image2_client.healthz()` 检查在线状态。app.py 中所有 image2 调用均为后台任务，前端通过轮询 `/api/tasks/{id}` 或 `/api/recommend` 的 `status` 字段获取结果。

**Groq API**：两处调用均在失败时自动降级，不崩溃：
- `fashion_router.py`：LLM 分类 → 规则分类兜底
- `outfit_recommender.py`：LLM caption → 规则 caption 兜底
- `phase1_wardrobe.py`：Groq Vision（`llama-4-scout-17b-16e-instruct`）识别单品

**DB 路径**：`wardrobe.db` 相对于运行目录，必须从 `5.14MVP/` 下执行命令。

**会话**：`_sessions` 是内存字典（`{session_id: ctx}`），重启清零。`ctx["current_item_ids"]` 是 swap_item 正常工作的前提，前端须在每次 POST /chat 时回传。

## Wardrobe Item Data Format

批量导入格式（`items.json`）：

```json
{
  "category": "上装|下装|全身|外套|鞋履|配件",
  "type": "衬衫|T恤|...",
  "raw_type": "原始识别名称",
  "color": ["白色"],
  "style": ["法式"],
  "season": ["春", "秋"],
  "warmth": "薄|中等|厚|不适用|无法判断",
  "fit": "修身|常规|宽松|oversize|不适用|无法判断",
  "description": "15-30字描述",
  "image": "001.jpg"
}
```

图片统一放 `images/` 文件夹，与 `items.json` 同级。

## Product Principles（来自 PRD v1.0）

- **零摩擦**：能自动化的不让用户手动填，能延迟的决策不放主流程
- **Image first**：AI 文字说明默认极短，长解释点开才展开
- **Subtle AI 语气**：brief / tasteful / minimal，不做陪伴人格化
