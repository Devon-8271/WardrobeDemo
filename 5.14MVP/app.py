"""
app.py  —  Fashion Agent Web 入口

启动：
  cd 5.14MVP && uvicorn app:app --reload --port 8000

接口：
  GET  /health                  Skill 健康检查（Hub 注册时调用）
  GET  /manifest                SkillManifest（Hub 注册时拉取）
  POST /chat                    主聊天入口（返回 AgentResponse）
  POST /upload/photo            上传用户全身照
  POST /upload/wardrobe-item    上传单品图 → Groq Vision 识别
  POST /confirm/wardrobe-item   确认识别结果 → 存入 DB
  GET  /wardrobe/upload         上传页面
  GET  /                        聊天页面
  GET  /images/...              生成图静态文件
"""

import asyncio
import uuid
import requests
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Request, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import db
import fashion_dispatch
import look_manager
import outfit_recommender
import outfit_generator
import phase1_wardrobe
import image2_client
import style_identity

# ── 初始化 ─────────────────────────────────────────────────────────────────────

# 正在 beautify 的单品 ID 集合（内存，重启清零）
_beautifying: set = set()

db.init_db()
app = FastAPI(title="Fashion Agent")

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    print(f"[422] {request.method} {request.url.path} — {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

BASE_DIR = Path(__file__).parent
app.mount("/images",      StaticFiles(directory=BASE_DIR / "images"),      name="images")
app.mount("/static",      StaticFiles(directory=BASE_DIR / "static"),      name="static")
(BASE_DIR / "user_photos").mkdir(exist_ok=True)
app.mount("/user_photos", StaticFiles(directory=BASE_DIR / "user_photos"), name="user_photos")
(BASE_DIR / "ootd_photos").mkdir(exist_ok=True)
app.mount("/ootd_photos", StaticFiles(directory=BASE_DIR / "ootd_photos"), name="ootd_photos")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 内存 session：{session_id: {"current_item_ids": [], "user_photo": "", ...}}
_sessions: dict = {}

# ── Quick tryon 结果缓存 ───────────────────────────────────────────────────────
# key = "{user_photo_md5}_{item_md5}"，value = 本地图片路径
# 重启后依然有效，相同 (用户照, 衣物图) 组合不再调 image2

import hashlib, json as _json

_TRYON_CACHE_FILE = BASE_DIR / "images" / "tryon_cache.json"
_TRYON_CACHE: dict = {}


def _load_tryon_cache() -> None:
    global _TRYON_CACHE
    if _TRYON_CACHE_FILE.is_file():
        try:
            _TRYON_CACHE = _json.loads(_TRYON_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _TRYON_CACHE = {}


def _save_tryon_cache() -> None:
    try:
        _TRYON_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TRYON_CACHE_FILE.write_text(
            _json.dumps(_TRYON_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        print(f"[tryon_cache] save failed: {e}")


def _file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tryon_cache_key(user_photo: str, item_path: str) -> str:
    return f"{_file_md5(user_photo)}_{_file_md5(item_path)}"


_load_tryon_cache()

def _get_session(sid: str) -> dict:
    if sid not in _sessions:
        _sessions[sid] = {}
    return _sessions[sid]


# ── 天气自动获取 ───────────────────────────────────────────────────────────────

_weather_cache: dict = {"data": None, "ts": 0.0}
_WEATHER_TTL = 120  # 两分钟内复用同一份天气，防止温度微浮动跨 bucket 导致 cache key 不同

def _fetch_weather() -> dict:
    import time
    now = time.time()
    if _weather_cache["data"] and now - _weather_cache["ts"] < _WEATHER_TTL:
        return _weather_cache["data"]
    try:
        r = requests.get("https://wttr.in/?format=j1", timeout=4)
        data = r.json()
        d    = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]
        city = area.get("areaName", [{}])[0].get("value", "")
        result = {"temp_c": float(d["temp_C"]), "description": d["weatherDesc"][0]["value"], "city": city}
    except Exception:
        result = {"temp_c": 20, "description": "晴", "city": ""}
    _weather_cache.update({"data": result, "ts": now})
    return result


def _fetch_weather_for_day(offset_days: int = 0) -> dict:
    """获取未来某天天气。0=今天（走 current_condition），1=明天，2=后天。"""
    if offset_days == 0:
        return _fetch_weather()
    try:
        r = requests.get("https://wttr.in/?format=j1", timeout=4)
        data = r.json()
        ws = data.get("weather", [])
        if 0 <= offset_days < len(ws):
            w = ws[offset_days]
            temp = float(w.get("avgtempC", "20"))
            hourly = w.get("hourly", [])
            desc = (hourly[len(hourly)//2].get("weatherDesc", [{}])[0].get("value", "")
                    if hourly else "")
            return {"temp_c": temp, "description": desc}
    except Exception:
        pass
    return {"temp_c": 20, "description": "晴"}


# ── 图片路径 → URL ─────────────────────────────────────────────────────────────

def _to_url(local_path: str) -> str:
    """把 images/grid/cell_xxx.png 转成 /images/grid/cell_xxx.png"""
    if not local_path:
        return ""
    p = Path(local_path)
    # 绝对路径：取相对于 BASE_DIR 的部分
    if p.is_absolute():
        try:
            return "/" + str(p.relative_to(BASE_DIR))
        except ValueError:
            return "/images/" + p.name
    # 已是相对路径（如 images/grid/cell_xxx.png）：直接加 /
    s = str(p)
    if s.startswith("images/"):
        return "/" + s
    return "/images/" + p.name


# ── /health & /manifest ────────────────────────────────────────────────────────

_SKILL_MANIFEST = {
    "skill_id":        "fashion",
    "name":            "穿搭助手",
    "description":     "管理用户衣橱、推荐今日搭配、支持换单品和虚拟试穿",
    "trigger_examples": [
        "今天穿什么",
        "帮我搭一套",
        "换条裤子",
        "试穿这件",
        "我衣橱里有什么",
        "保存这套",
    ],
    "endpoint":   "http://fashion-agent:8000",
    "health_url": "http://fashion-agent:8000/health",
    "version":    "1.0",
    "card_types": [
        "outfit_recommendation",   # 推荐搭配轮播
        "outfit_update",           # 换单品后的更新搭配
        "try_on_result",           # 虚拟试穿结果
        "wardrobe_list",           # 衣橱查询列表
    ],
    "task_types": ["try_on", "swap_outfit"],
    "attachment": {
        "supported":           True,
        "modes":               ["hub_managed", "direct_to_skill"],
        "max_size_mb":         20,
        "accepted_mime_types": ["image/jpeg", "image/png", "image/webp"],
        "purposes":            ["try_on", "wardrobe_item", "profile_photo"],
        "upload_endpoint":     "/attachments/upload",
    },
    "events": [
        {"event": "swap_item",  "required_params": ["entity_id"],           "optional_params": ["slot"], "async": False},
        {"event": "try_on",     "required_params": ["entity_id"],                                        "async": True },
        {"event": "save_look",  "required_params": ["entity_id"],                                        "async": False},
        {"event": "retry_task", "required_params": ["task_id"],                                          "async": True },
    ],
}


@app.get("/health")
def health():
    """Hub 注册时调用，检查 Skill 在线状态及依赖服务。"""
    wardrobe_count = len(db.get_all_wardrobe_items())
    image2_ok      = phase1_wardrobe.image2_client.healthz()
    return {
        "status":         "ok",
        "skill_id":       "fashion",
        "version":        _SKILL_MANIFEST["version"],
        "wardrobe_items": wardrobe_count,
        "image2_online":  image2_ok,
    }


@app.get("/manifest")
def manifest():
    """返回 SkillManifest，Hub 注册时拉取。"""
    return _SKILL_MANIFEST


# ── /chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str = ""
    message: str
    current_item_ids: list = []   # 兼容旧前端；新前端通过 context.entities 传入
    save_date: str = ""           # 保存穿搭时的自定义日期，格式 YYYY-MM-DD

class ChatResponse(BaseModel):
    session_id: str
    skill: str = "fashion"
    action: str
    message: str = ""
    cards: list = []
    context_update: Optional[dict] = None
    error: Optional[dict] = None

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    ctx = _get_session(sid)

    # 兼容旧前端：current_item_ids 注入为 session 旧格式
    if req.current_item_ids:
        ctx["current_item_ids"] = req.current_item_ids
    if req.save_date:
        ctx["save_date"] = req.save_date

    weather = _fetch_weather()

    resp = fashion_dispatch.dispatch(
        user_input=req.message,
        context=ctx,
        weather=weather,
        dry_run=True,
    )

    # 富化 cards[].items[].image 路径 → URL，metadata 补单品图
    if resp.get("cards"):
        all_items = db.get_all_wardrobe_items(source_filter="real")
        item_img  = {it["item_id"]: _to_url(it.get("image_url") or it.get("image_crop_url") or "")
                     for it in all_items}
        item_type = {it["item_id"]: it.get("type", "") for it in all_items}

        for card in resp["cards"]:
            for item in card.get("items", []):
                if item.get("image"):
                    item["image"] = _to_url(item["image"])
                meta = item.setdefault("metadata", {})
                if meta.get("item_ids"):
                    meta["item_images"] = [item_img.get(iid, "") for iid in meta["item_ids"] if item_img.get(iid)]
                    meta["item_types"]  = [item_type.get(iid, "") for iid in meta["item_ids"]]

    # 将 context_update patch merge 进 session
    ctx_patch = resp.get("context_update")
    if ctx_patch:
        for k, v in ctx_patch.items():
            if v is None:
                ctx.pop(k, None)
            elif isinstance(v, dict) and isinstance(ctx.get(k), dict):
                ctx[k] = {**ctx[k], **v}
            else:
                ctx[k] = v
        # 兼容旧 current_item_ids：从 entities 同步
        eid = ctx.get("current_entity_id")
        if eid and ctx.get("entities", {}).get(eid):
            ids = ctx["entities"][eid].get("metadata", {}).get("item_ids")
            if ids:
                ctx["current_item_ids"] = ids

    if resp["action"] == "save_look":
        ctx.pop("current_item_ids", None)
        ctx.pop("current_entity_id", None)

    return {
        "session_id":     sid,
        "skill":          resp["skill"],
        "action":         resp["action"],
        "message":        resp.get("message", ""),
        "cards":          resp.get("cards", []),
        "context_update": resp.get("context_update"),
        "error":          resp.get("error"),
    }


# ── /upload/photo ──────────────────────────────────────────────────────────────

@app.post("/upload/photo")
async def upload_photo(file: UploadFile = File(...), session_id: str = ""):
    sid = session_id or str(uuid.uuid4())
    save_dir = BASE_DIR / "user_photos"
    save_dir.mkdir(exist_ok=True)

    ext = Path(file.filename).suffix or ".jpg"
    filename = f"photo_{uuid.uuid4().hex}{ext}"
    dest = save_dir / filename

    with open(dest, "wb") as f:
        f.write(await file.read())

    # 窄更新：新上传自动设为主照，保留 height / body_type / 等其他字段
    db.update_user_photo("default", str(dest), datetime.now().isoformat())
    outfit_recommender.clear_cache()   # 主照换了，已生成的上身图作废
    _get_session(sid)["user_photo"] = str(dest)

    return {
        "session_id": sid,
        "photo_url": f"/user_photos/{filename}",
        "filename": filename,
    }


# ── /upload/wardrobe-item ──────────────────────────────────────────────────────

@app.post("/upload/wardrobe-item")
async def upload_wardrobe_item(file: UploadFile = File(...)):
    """上传单品图 → Groq Vision 识别 → 返回识别结果供用户确认。"""
    import asyncio
    save_dir = BASE_DIR / "images"
    save_dir.mkdir(exist_ok=True)

    ext      = Path(file.filename).suffix or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest     = save_dir / filename

    with open(dest, "wb") as f:
        f.write(await file.read())

    # 阻塞调用放线程池，避免堵塞事件循环
    items = await asyncio.to_thread(phase1_wardrobe.recognize_clothing, str(dest))
    if not items:
        return {"ok": False, "error": "识别失败，请上传更清晰的照片", "items": []}

    return {
        "ok":       True,
        "image_url": f"/images/{filename}",
        "image_path": str(dest),
        "items":    items,
    }


def _beautify_and_update(item_id: str, image_path: str):
    """后台任务：调 image2 美化原图，完成后更新 DB image_url。"""
    try:
        print(f"  [beautify] 开始美化 {item_id}...")
        item = db.get_wardrobe_item(item_id) or {}
        color = "、".join(item.get("color") or [])
        desc  = item.get("description") or item.get("type") or ""
        label = f"{color}{desc}" if color else desc
        beautified = phase1_wardrobe.beautify_image(image_path, description=label)
        conn = db.get_conn()
        conn.execute("UPDATE wardrobe SET image_url = ? WHERE item_id = ?",
                     (beautified, item_id))
        conn.commit()
        conn.close()
        print(f"  [beautify] 完成 {item_id} → {beautified}")
    except Exception as e:
        print(f"  [beautify] 失败 {item_id}: {e}")
    finally:
        _beautifying.discard(item_id)


@app.post("/confirm/wardrobe-item")
async def confirm_wardrobe_item(
    background_tasks: BackgroundTasks,
    image_path: str      = Form(...),
    category:   str      = Form(...),
    type_:      str      = Form(..., alias="type"),
    color:      str      = Form(...),
    style:      str      = Form(...),
    season:     str      = Form(...),
    warmth:     str      = Form("中等"),
    fit:        str      = Form("常规"),
    description:str      = Form(...),
):
    """确认识别结果，写入 DB，后台触发 beautify。"""
    import json as _json
    def _parse(s):
        try:
            v = _json.loads(s)
            return v if isinstance(v, list) else [s]
        except Exception:
            return [x.strip() for x in s.split(",") if x.strip()]

    item_id = str(uuid.uuid4())
    item = {
        "item_id":    item_id,
        "category":   category,
        "type":       type_,
        "raw_type":   type_,
        "color":      _parse(color),
        "style":      _parse(style),
        "season":     _parse(season),
        "warmth":     warmth,
        "fit":        fit,
        "description":description,
        "image_url":  image_path,   # 先存原图
        "source":     "real",
        "upload_time":datetime.now().isoformat(),
    }
    db.insert_wardrobe_item(item)
    outfit_recommender.invalidate_outfits()  # 衣橱变动，重算 outfit，图片能复用则保留

    # 后台美化（image2 服务可用时才触发）
    if phase1_wardrobe.image2_client.healthz():
        _beautifying.add(item_id)
        background_tasks.add_task(_beautify_and_update, item_id, image_path)
        beautifying = True
    else:
        beautifying = False

    return {"ok": True, "item_id": item_id, "type": type_, "beautifying": beautifying}


# ── 异步任务层（v1.1 用 polling；将来对接 Hub 时只需把 _emit_task_done 改成 webhook 推送）─

# in-memory 任务表。重启清零，demo 用够了；未来要持久化时迁到 SQLite
_tasks: dict = {}


def _emit_task_done(task: dict) -> None:
    """任务完成回调钩子。
    v1.1：no-op（前端 polling 自取）。
    对接 AI Hub 时改成 requests.post(hub_callback_url, json=task)。"""
    pass


def _run_quick_tryon(task_id: str, user_photo: str, input_path: str) -> None:
    """BG task：随手试穿，无 item 元数据，调 tryon_skill。"""
    import tryon_skill
    task = _tasks.get(task_id)
    if not task:
        return
    try:
        task["status"] = "running"

        # 命中缓存则直接复用，跳过 image2
        ck = _tryon_cache_key(user_photo, input_path)
        if ck in _TRYON_CACHE and Path(_TRYON_CACHE[ck]).is_file():
            task["status"]       = "completed"
            task["result_url"]   = _to_url(_TRYON_CACHE[ck])
            task["completed_at"] = datetime.now().isoformat()
            _emit_task_done(task)
            return

        out_path = tryon_skill.run(
            person_photo=user_photo,
            item_images=[input_path],
        )
        _TRYON_CACHE[ck] = out_path
        _save_tryon_cache()

        task["status"]       = "completed"
        task["result_url"]   = _to_url(out_path)
        task["completed_at"] = datetime.now().isoformat()
    except Exception as e:
        task["status"]       = "failed"
        task["error"]        = {"code": "IMAGE_GENERATION_FAILED", "message": f"生成失败：{e}"}
        task["completed_at"] = datetime.now().isoformat()
    _emit_task_done(task)


def _run_tryon_outfit(task_id: str, user_photo: str, item_ids: list) -> None:
    """BG task：推荐套装试穿，调 tryon_skill（含 pose_engine OOTD 上下文）。"""
    import tryon_skill
    task = _tasks.get(task_id)
    if not task:
        return
    try:
        task["status"] = "running"

        items = [db.get_wardrobe_item(iid) for iid in item_ids]
        items = [it for it in items if it]
        item_images = [
            it["image_url"] for it in items
            if it.get("image_url") and Path(it["image_url"]).is_file()
        ]

        out_path = tryon_skill.run(
            person_photo=user_photo,
            item_images=item_images,
            items=items,
        )
        task["status"]       = "completed"
        task["result_url"]   = _to_url(out_path)
        task["completed_at"] = datetime.now().isoformat()
    except Exception as e:
        task["status"]       = "failed"
        task["error"]        = {"code": "IMAGE_GENERATION_FAILED", "message": str(e)}
        task["completed_at"] = datetime.now().isoformat()
    _emit_task_done(task)


def _run_preview_tryon(task_id: str, user_photo: str, image_path: str, items: list) -> None:
    """BG task：上传后试穿预览，支持多件单品同时上身。"""
    import tryon_skill
    task = _tasks.get(task_id)
    if not task:
        return
    try:
        task["status"] = "running"
        out_path = tryon_skill.run(
            person_photo=user_photo,
            item_images=[image_path],
            items=items,
        )
        task["status"]       = "completed"
        task["result_url"]   = _to_url(out_path)
        task["completed_at"] = datetime.now().isoformat()
    except Exception as e:
        task["status"]       = "failed"
        task["error"]        = {"code": "IMAGE_GENERATION_FAILED", "message": str(e)}
        task["completed_at"] = datetime.now().isoformat()
    _emit_task_done(task)


class PreviewTryonRequest(BaseModel):
    image_path: str
    item: dict = {}     # 单件（legacy）
    items: list = []    # 多件（新，优先使用）

@app.post("/api/tasks/preview-tryon")
def submit_preview_tryon(req: PreviewTryonRequest, background_tasks: BackgroundTasks):
    """上传识别后的试穿预览：image_path 已在服务器，不需要重传文件。"""
    if not req.image_path or not Path(req.image_path).is_file():
        return {"ok": False, "error": "图片路径无效"}

    profile = db.get_user_profile("default") or {}
    user_photo = profile.get("photo_url") or ""
    if not user_photo or not Path(user_photo).is_file():
        return {"ok": False, "error": "请先去「我的形象」上传全身照"}
    if not phase1_wardrobe.image2_client.healthz():
        return {"ok": False, "error": "image2 服务暂不可达，请检查 Wi-Fi"}

    items = req.items if req.items else ([req.item] if req.item else [])

    task_id = uuid.uuid4().hex
    _tasks[task_id] = {
        "task_id":      task_id,
        "kind":         "preview_tryon",
        "status":       "queued",
        "result_url":   "",
        "error":        None,
        "created_at":   datetime.now().isoformat(),
        "completed_at": "",
    }
    background_tasks.add_task(_run_preview_tryon, task_id, user_photo, req.image_path, items)
    return {"ok": True, "task_id": task_id}


@app.post("/api/tasks/quick-tryon")
async def submit_quick_tryon(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """提交随手试穿任务，立即返回 task_id；前端轮询 /api/tasks/{id} 取结果。
    不写入衣橱。"""
    # 前置检查
    profile = db.get_user_profile("default") or {}
    user_photo = profile.get("photo_url") or ""
    if not user_photo or not Path(user_photo).is_file():
        return {"ok": False, "error": "请先去「我的形象」上传一张全身照"}
    if not phase1_wardrobe.image2_client.healthz():
        return {"ok": False, "error": "image2 服务暂不可达，请稍后重试"}

    # 保存衣物图到独立目录
    save_dir = BASE_DIR / "images" / "tryon_inputs"
    save_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix or ".jpg"
    src = save_dir / f"tryon_in_{uuid.uuid4().hex}{ext}"
    with open(src, "wb") as f:
        f.write(await file.read())

    # 创建任务
    task_id = uuid.uuid4().hex
    _tasks[task_id] = {
        "task_id":      task_id,
        "kind":         "quick_tryon",
        "status":       "queued",
        "input_url":    _to_url(str(src)),
        "result_url":   "",
        "error":        None,
        "created_at":   datetime.now().isoformat(),
        "completed_at": "",
    }

    background_tasks.add_task(_run_quick_tryon, task_id, user_photo, str(src))
    return {"ok": True, "task_id": task_id}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    """查询任务状态。前端按 5s 间隔轮询。"""
    task = _tasks.get(task_id)
    if not task:
        # 返回 status="expired" 让前端停止轮询，而不是无限等待
        return {"ok": False, "status": "expired", "error": "任务不存在或已过期"}
    return {"ok": True, **task}


# ── /api/hub/action Hub 按钮事件路由 ──────────────────────────────────────────

class HubActionRequest(BaseModel):
    event:   str
    params:  dict = {}
    user_id: str  = "default"
    context: dict = {}

@app.post("/api/hub/action")
def hub_action(req: HubActionRequest, background_tasks: BackgroundTasks):
    """Hub 卡片按钮事件入口（try_on / swap_item / save_look）。
    try_on 创建后台任务，立即返回 task_id；前端轮询 /api/tasks/{id}。"""
    resp = fashion_dispatch.handle_action(req.event, req.params, req.user_id, req.context)

    if resp.get("action") == "try_on" and not resp.get("error"):
        cu         = resp.get("context_update") or {}
        item_ids   = cu.pop("_try_on_item_ids",   [])
        user_photo = cu.pop("_try_on_user_photo",  "")

        if item_ids and user_photo:
            task_id = uuid.uuid4().hex
            _tasks[task_id] = {
                "task_id":      task_id,
                "kind":         "tryon_outfit",
                "status":       "queued",
                "result_url":   "",
                "error":        None,
                "created_at":   datetime.now().isoformat(),
                "completed_at": "",
            }
            background_tasks.add_task(_run_tryon_outfit, task_id, user_photo, item_ids)
            resp["task"] = {
                "status":   "queued",
                "task_id":  task_id,
                "poll_url": f"/api/tasks/{task_id}",
            }

    return resp


# ── /api/wardrobe ──────────────────────────────────────────────────────────────

@app.get("/api/wardrobe")
def api_wardrobe(category: str = ""):
    items = db.get_all_wardrobe_items(source_filter="real")
    if category:
        items = [it for it in items if it.get("category") == category]
    for it in items:
        it["image_url"] = _to_url(it.get("image_url") or "")
        it["image_crop_url"] = _to_url(it.get("image_crop_url") or "")
        it["beautifying"] = it["item_id"] in _beautifying
    return {"items": items, "total": len(items)}


@app.delete("/api/wardrobe/{item_id}")
def delete_wardrobe_item(item_id: str):
    db.delete_wardrobe_item(item_id)
    outfit_recommender.invalidate_outfits()  # 衣橱变动，重算 outfit，图片能复用则保留
    return {"ok": True}


@app.post("/api/wardrobe/{item_id}/beautify")
def trigger_beautify(item_id: str, background_tasks: BackgroundTasks):
    """手动触发某单品 beautify（用于 beautify 代码加入前上传的单品）。"""
    item = db.get_wardrobe_item(item_id)
    if not item:
        return {"ok": False, "error": "item not found"}
    img = item.get("image_url", "")
    if not img or not Path(img).is_file():
        return {"ok": False, "error": "原图缺失或路径无效"}
    if item_id in _beautifying:
        return {"ok": False, "error": "已经在美化中"}
    if not phase1_wardrobe.image2_client.healthz():
        return {"ok": False, "error": "image2 服务不可达"}
    _beautifying.add(item_id)
    background_tasks.add_task(_beautify_and_update, item_id, img)
    return {"ok": True}




# ── /api/recommend ─────────────────────────────────────────────────────────────

def _generate_outfit_images_bg(
    cache_key: tuple,
    user_photo: str,
    outfits: list,
    occasion: str = None,
    is_weekend: bool = None,
) -> None:
    """后台任务：调 image2 生成拼图 → 裁切 → 写入 outfit_recommender 图缓存。"""
    try:
        local_paths = outfit_generator.generate_outfit_grid(
            user_photo=user_photo,
            outfits=outfits,
            occasion=occasion,
            is_weekend=is_weekend,
        )
        outfit_recommender.set_cached_images(cache_key, local_paths)
        print(f"[recommend] 拼图生成完成，{len(local_paths)} 张")
    except Exception as e:
        print(f"[recommend] 后台生图失败: {e}")
    finally:
        outfit_recommender.mark_generating(cache_key, False)


@app.get("/api/recommend")
def api_recommend(background_tasks: BackgroundTasks, n: int = 4, refresh: int = 0):
    """
    首页推荐数据：返回 n 套搭配 + 天气 + 风格档案 + 状态。

    懒触发：返回元数据秒返，自动起后台任务调 image2 生图；前端按 `status` 决定是否轮询。
    refresh=1 强制重算推荐 + 清图缓存（「换一组」按钮）。

    返回:
      {
        "weather":   {...},
        "style":     {...},
        "outfits":   [{"item_ids":[...], "style_tags":[...], "caption":"...",
                       "item_images":["/images/..."]}, ...],
        "images":    ["/images/grid/cell_xxx.png", ...],
        "status":    "completed" | "running" | "failed",
        "error":     {"code": "NO_WARDROBE|NO_USER_PHOTO|IMAGE_SERVICE_OFFLINE", "message": "..."}  // failed 时
      }
    """
    from datetime import date as _date, datetime as _datetime

    weather    = _fetch_weather()
    _today     = _date.today()
    _now_hour  = _datetime.now().hour
    is_weekend = _today.weekday() >= 5
    outfits = outfit_recommender.recommend_outfits(
        user_id="default", weather=weather, n=n, refresh=bool(refresh),
    )

    # 晚 TOMORROW_PLANNING_HOUR 点后、且今日已有确认穿搭 → 切换到明日推荐
    # 防止用户今天还没出门就看到明日推荐
    if _now_hour >= TOMORROW_PLANNING_HOUR and db.has_look_on_date(_today.isoformat()):
        _target_date = (_today + timedelta(days=1)).isoformat()
    else:
        _target_date = _today.isoformat()

    # refresh：清掉旧图缓存，让重算后的推荐重新生图
    key = outfit_recommender.cache_key("default", _target_date, weather["temp_c"])
    if refresh:
        outfit_recommender.set_cached_images(key, [])

    # 富化：每套附 item_images 供前端回退展示
    all_items = db.get_all_wardrobe_items(source_filter="real")
    item_img = {
        it["item_id"]: _to_url(it.get("image_url") or it.get("image_crop_url") or "")
        for it in all_items
    }
    item_type = {it["item_id"]: it.get("type", "") for it in all_items}
    for o in outfits:
        o["item_images"] = [item_img.get(iid, "") for iid in o["item_ids"] if item_img.get(iid)]
        o["item_types"]  = [item_type.get(iid, "") for iid in o["item_ids"]]

    images: list = []
    status = "completed"
    error  = None

    if not outfits:
        status = "failed"
        error  = {"code": "NO_WARDROBE", "message": "衣橱还没有单品"}
    else:
        # 1) 查图缓存（验证文件还在，且数量与当前 outfits 匹配）
        cached_paths = outfit_recommender.get_cached_images(key)
        valid_paths  = [p for p in cached_paths if Path(p).is_file()]
        if valid_paths and len(valid_paths) == len(outfits):
            images = [_to_url(p) for p in valid_paths]
        else:
            # 缓存图少于当前 outfits 数量（e.g. 之前只生了 1 套，现在有 4 套）→ 清缓存重生
            if cached_paths:
                outfit_recommender.set_cached_images(key, [])

            # 2) 决定是否触发后台生图（claim_generating 原子 check-and-set，防并发重复触发）
            if outfit_recommender.is_generating(key):
                status = "running"
            else:
                profile = db.get_user_profile("default") or {}
                user_photo = profile.get("photo_url", "")
                if not user_photo or not Path(user_photo).is_file():
                    status = "failed"
                    error  = {"code": "NO_USER_PHOTO", "message": "请先上传全身照"}
                elif not phase1_wardrobe.image2_client.healthz():
                    status = "failed"
                    error  = {"code": "IMAGE_SERVICE_OFFLINE", "message": "图像服务暂不可达，请检查 Wi-Fi"}
                elif outfit_recommender.claim_generating(key):
                    background_tasks.add_task(
                        _generate_outfit_images_bg, key, user_photo, list(outfits),
                        None, is_weekend,
                    )
                    status = "running"
                else:
                    status = "running"

    style = style_identity.compute_style_identity("default")

    return {
        "weather": weather,
        "style":   style,
        "outfits": outfits,
        "images":  images,
        "status":  status,
        "error":   error,
    }


# ── /api/recommend/swap ────────────────────────────────────────────────────────

class SwapRequest(BaseModel):
    item_id: str              # 要换掉的单品 ID
    outfit_item_ids: list     # 当前这套 outfit 的完整 item_ids

@app.post("/api/recommend/swap")
def api_recommend_swap(req: SwapRequest):
    """首页推荐卡片单品替换：点某件单品 → 同品类随机换一件，秒返不调 image2。"""
    item = db.get_wardrobe_item(req.item_id)
    if not item:
        return {"ok": False, "message": "单品不存在"}

    weather = _fetch_weather()
    route = {"category": item.get("category", "")}
    result = fashion_dispatch._handle_swap_item(
        "default", req.outfit_item_ids, {}, route, weather,
    )

    # 从 AgentResponse cards[0].items[0].metadata 读取结果
    meta = {}
    if result.get("cards") and result["cards"][0].get("items"):
        meta = result["cards"][0]["items"][0].get("metadata", {})

    new_item_ids = meta.get("item_ids", req.outfit_item_ids)
    available    = meta.get("available", 0)

    all_items = db.get_all_wardrobe_items(source_filter="real")
    item_img  = {it["item_id"]: _to_url(it.get("image_url") or it.get("image_crop_url") or "")
                 for it in all_items}

    payload = {
        **meta,
        "item_images": [item_img.get(iid, "") for iid in new_item_ids if item_img.get(iid)],
    }
    return {"ok": available > 0 or not result.get("error"), "payload": payload, "message": result["message"]}


# ── /api/tasks/swap-outfit ─────────────────────────────────────────────────────

class SwapOutfitTaskRequest(BaseModel):
    item_ids:    list   # 换完后的完整 item_ids
    new_item_id: str    # 新换入的单品 ID（用于记录，不影响生图）

def _run_swap_outfit(task_id: str, user_photo: str, item_ids: list) -> None:
    task = _tasks.get(task_id)
    if not task:
        return
    try:
        task["status"] = "running"
        outfit = {"item_ids": item_ids, "style_tags": [], "caption": ""}
        paths = outfit_generator.generate_outfit_grid(user_photo, [outfit])
        if paths:
            task["status"]       = "completed"
            task["result_url"]   = _to_url(paths[0])
        else:
            task["status"]       = "failed"
            task["error"]        = {"code": "IMAGE_GENERATION_FAILED", "message": "生成失败，请重试"}
        task["completed_at"] = datetime.now().isoformat()
    except Exception as e:
        task["status"]       = "failed"
        task["error"]        = {"code": "IMAGE_GENERATION_FAILED", "message": str(e)}
        task["completed_at"] = datetime.now().isoformat()
    _emit_task_done(task)

@app.post("/api/tasks/swap-outfit")
def submit_swap_outfit(req: SwapOutfitTaskRequest, background_tasks: BackgroundTasks):
    """换装后异步生图：用新 item_ids 调 image2，结果推送到聊天卡片。"""
    profile = db.get_user_profile("default") or {}
    user_photo = profile.get("photo_url") or ""
    if not user_photo or not Path(user_photo).is_file():
        return {"ok": False, "error": "请先去「我的形象」上传全身照"}
    if not phase1_wardrobe.image2_client.healthz():
        return {"ok": False, "error": "image2 服务暂不可达，请检查 Wi-Fi"}

    task_id = uuid.uuid4().hex
    _tasks[task_id] = {
        "task_id":      task_id,
        "kind":         "swap_outfit",
        "status":       "queued",
        "input_url":    "",
        "result_url":   "",
        "error":        None,
        "created_at":   datetime.now().isoformat(),
        "completed_at": "",
    }
    background_tasks.add_task(_run_swap_outfit, task_id, user_photo, req.item_ids)
    return {"ok": True, "task_id": task_id}


# ── /api/tasks/tryon-outfit ────────────────────────────────────────────────────

class TryonOutfitTaskRequest(BaseModel):
    item_ids: list   # 套装单品 ID 列表

@app.post("/api/tasks/tryon-outfit")
def submit_tryon_outfit(req: TryonOutfitTaskRequest, background_tasks: BackgroundTasks):
    """推荐套装试穿：用 item_ids 调 image2（含 pose_engine 姿势建议），前端轮询 /api/tasks/{id}。"""
    if not req.item_ids:
        return {"ok": False, "error": "item_ids 不能为空"}

    profile = db.get_user_profile("default") or {}
    user_photo = profile.get("photo_url") or ""
    if not user_photo or not Path(user_photo).is_file():
        return {"ok": False, "error": "请先去「我的形象」上传全身照"}
    if not phase1_wardrobe.image2_client.healthz():
        return {"ok": False, "error": "image2 服务暂不可达，请检查 Wi-Fi"}

    task_id = uuid.uuid4().hex
    _tasks[task_id] = {
        "task_id":      task_id,
        "kind":         "tryon_outfit",
        "status":       "queued",
        "result_url":   "",
        "error":        None,
        "created_at":   datetime.now().isoformat(),
        "completed_at": "",
    }
    background_tasks.add_task(_run_tryon_outfit, task_id, user_photo, req.item_ids)
    return {"ok": True, "task_id": task_id}


# ── /looks 穿搭日志页 ──────────────────────────────────────────────────────────

@app.get("/looks", response_class=HTMLResponse)
def looks_page(request: Request):
    return templates.TemplateResponse("looks.html", {"request": request})


# ── /api/profile + /profile 我的形象 ───────────────────────────────────────────

def _photo_url_for_file(filename: str) -> str:
    return f"/user_photos/{filename}" if filename else ""


@app.get("/api/profile")
def api_profile():
    p = db.get_user_profile("default") or {}
    photo = p.get("photo_url") or ""
    return {
        "has_photo": bool(photo),
        "photo_url": _to_url(photo),
        "upload_time": p.get("upload_time") or "",
    }


@app.get("/api/photos")
def api_photos():
    """列出所有用户照片，标记当前主照。"""
    save_dir = BASE_DIR / "user_photos"
    if not save_dir.is_dir():
        return {"photos": [], "key_filename": ""}

    p = db.get_user_profile("default") or {}
    key_path = p.get("photo_url") or ""
    key_filename = Path(key_path).name if key_path else ""

    files = sorted(
        [f for f in save_dir.iterdir() if f.is_file() and not f.name.startswith(".")],
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    photos = [{
        "filename": f.name,
        "url": _photo_url_for_file(f.name),
        "is_key": f.name == key_filename,
        "uploaded_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
    } for f in files]

    return {"photos": photos, "key_filename": key_filename}


@app.post("/api/photos/key")
def set_key_photo(filename: str = Form(...)):
    save_dir = BASE_DIR / "user_photos"
    target = save_dir / filename
    if not target.is_file():
        return {"ok": False, "error": "照片不存在"}
    db.update_user_photo("default", str(target), datetime.now().isoformat())
    outfit_recommender.clear_cache()   # 主照换了，已生成的上身图作废
    return {"ok": True, "filename": filename}


@app.delete("/api/photos/{filename}")
def delete_photo(filename: str):
    save_dir = BASE_DIR / "user_photos"
    target = save_dir / filename
    if not target.is_file():
        return {"ok": False, "error": "照片不存在"}

    p = db.get_user_profile("default") or {}
    key_path = p.get("photo_url") or ""
    was_key = (Path(key_path).name == filename) if key_path else False

    target.unlink()

    # 删的是主照时自动晋升另一张（按修改时间最新）；都没了就清空
    if was_key:
        remaining = sorted(
            [f for f in save_dir.iterdir() if f.is_file() and not f.name.startswith(".")],
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        new_key = str(remaining[0]) if remaining else ""
        db.update_user_photo("default", new_key, datetime.now().isoformat())
        outfit_recommender.clear_cache()   # 主照换了，已生成的上身图作废

    return {"ok": True}


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    return templates.TemplateResponse("profile.html", {"request": request})


@app.get("/tryon", response_class=HTMLResponse)
def tryon_page(request: Request):
    return templates.TemplateResponse("tryon.html", {"request": request})


# ── /wardrobe/upload 上传页 ────────────────────────────────────────────────────

@app.get("/wardrobe/upload", response_class=HTMLResponse)
def wardrobe_upload_page(request: Request):
    return templates.TemplateResponse("wardrobe_upload.html", {"request": request})


# ── /wardrobe 衣橱页 ───────────────────────────────────────────────────────────

@app.get("/wardrobe", response_class=HTMLResponse)
def wardrobe_page(request: Request):
    return templates.TemplateResponse("wardrobe.html", {"request": request})


# ── /looks 穿搭日志 ────────────────────────────────────────────────────────────

@app.get("/api/looks")
def api_looks(scene: str = "", limit: int = 60):
    looks = db.get_looks(user_id="default", scene=scene or None, limit=limit)
    all_items = {it["item_id"]: it for it in db.get_all_wardrobe_items(source_filter="real")}
    for lk in looks:
        lk["photo_url"] = _to_url(lk.get("photo_url") or "")
        lk["tryon_url"] = _to_url(lk.get("tryon_url") or "")
        lk["items"] = [
            {
                "item_id":  iid,
                "type":     all_items[iid].get("type", ""),
                "color":    all_items[iid].get("color", []),
                "category": all_items[iid].get("category", ""),
                "image_url": _to_url(
                    all_items[iid].get("image_crop_url") or all_items[iid].get("image_url") or ""
                ),
            }
            for iid in lk.get("item_ids", []) if iid in all_items
        ]
    return {"looks": looks, "total": len(looks)}


class SaveLookRequest(BaseModel):
    item_ids:  list = []
    photo_url: str  = ""
    date:      str  = ""
    scene:     str  = ""
    user_id:   str  = "default"

class OotdItemRequest(BaseModel):
    category: str = ""
    type: str = ""
    color: list = []
    style: list = []
    season: list = []
    warmth: str = ""
    fit: str = ""
    material: list = []
    description: str = ""
    matched_item_id: str = ""
    is_new: bool = True


class SaveOotdLookRequest(BaseModel):
    photo_url: str = ""
    date: str = ""
    scene: str = ""
    user_id: str = "default"
    items: list[OotdItemRequest] = []

@app.post("/api/looks")
def api_save_look(req: SaveLookRequest, background_tasks: BackgroundTasks):
    look_id = look_manager.save_look(
        user_id=req.user_id,
        item_ids=req.item_ids,
        photo_url=req.photo_url or None,
        scene=req.scene or None,
        source="manual",
        look_date=req.date or None,
    )
    return {"ok": True, "look_id": look_id}

def _simple_match_wardrobe_item(candidate: dict, wardrobe_items: list):
    c_type = str(candidate.get("type", "")).strip()
    c_colors = set(candidate.get("color") or [])

    for item in wardrobe_items:
        if str(item.get("type", "")).strip() != c_type:
            continue

        item_colors = set(item.get("color") or [])
        if c_colors and item_colors and c_colors & item_colors:
            return item

    return None

@app.post("/api/looks/recognize-ootd")
async def api_recognize_ootd(
    file: UploadFile = File(...),
    date: str = Form(""),
    scene: str = Form(""),
):
    import asyncio
    from phase1_wardrobe import recognize_ootd_items

    save_dir = BASE_DIR / "ootd_photos"
    save_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower() or ".jpg"
    filename = f"ootd_{uuid.uuid4().hex}{ext}"
    dest = save_dir / filename

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    photo_url = _to_url(str(dest))
    items = await asyncio.to_thread(recognize_ootd_items, str(dest))

    wardrobe_items = db.get_all_wardrobe_items(source_filter="real")
    enriched = []

    for item in items:
        matched = _simple_match_wardrobe_item(item, wardrobe_items)
        item = dict(item)
        if matched:
            item["matched_item_id"] = matched["item_id"]
            item["is_new"] = False
        else:
            item["matched_item_id"] = ""
            item["is_new"] = True
        enriched.append(item)

    return {
        "ok": True,
        "photo_url": photo_url,
        "date": date,
        "scene": scene,
        "items": enriched,
    }

def _url_to_path(url: str) -> str:
    """把 /ootd_photos/xxx.jpg 这类 URL 还原为本地绝对路径。"""
    if not url or url.startswith("http://") or url.startswith("https://"):
        return ""
    p = BASE_DIR / url.lstrip("/")
    return str(p) if p.is_file() else ""


def _generate_white_bg_for_item(item_id: str, ootd_photo_path: str, description: str) -> None:
    """后台任务：用 OOTD 原图 + 描述生成白底单品图，完成后更新 DB。"""
    try:
        prompt = (
            f"从图片的穿搭照中提取「{description}」，"
            "生成专业电商白底平铺商品图：纯白背景，光线均匀，"
            "衣物完整展开，保留原始颜色和印花细节，不改变设计。"
        )
        result_path = image2_client.generate(
            prompt=prompt,
            image_paths=[ootd_photo_path],
            out_dir=str(BASE_DIR / "images"),
            prefix="whitebg",
        )
        conn = db.get_conn()
        conn.execute("UPDATE wardrobe SET image_url = ? WHERE item_id = ?",
                     (result_path, item_id))
        conn.commit()
        conn.close()
        print(f"  [white_bg] 完成 {item_id} → {result_path}")
    except Exception as e:
        print(f"  [white_bg] 失败 {item_id}: {e}")


@app.post("/api/looks/save-from-ootd")
def api_save_look_from_ootd(req: SaveOotdLookRequest, background_tasks: BackgroundTasks):
    item_ids = []
    ootd_path = _url_to_path(req.photo_url)

    for item in req.items:
        data = item.model_dump()

        if data.get("matched_item_id") and not data.get("is_new"):
            item_ids.append(data["matched_item_id"])
            continue

        new_item_id = uuid.uuid4().hex
        wardrobe_item = {
            "item_id": new_item_id,
            "category": data.get("category", ""),
            "type": data.get("type", ""),
            "color": data.get("color", []),
            "style": data.get("style", []),
            "season": data.get("season", []),
            "warmth": data.get("warmth", ""),
            "fit": data.get("fit", ""),
            "material": data.get("material", []),
            "description": data.get("description", ""),
            "image_url": req.photo_url,  # 先用原图占位，后台替换为白底图
            "source": "real",
            "upload_time": datetime.now().isoformat(),
        }
        db.insert_wardrobe_item(wardrobe_item)
        item_ids.append(new_item_id)
        outfit_recommender.invalidate_outfits(req.user_id or "default")

        # 后台生成白底图
        if ootd_path:
            background_tasks.add_task(
                _generate_white_bg_for_item,
                new_item_id, ootd_path, data.get("description", data.get("type", "")),
            )

    look_id = look_manager.save_look(
        user_id=req.user_id,
        item_ids=item_ids,
        photo_url=req.photo_url or None,
        scene=req.scene or None,
        source="ootd",
        look_date=req.date or None,
    )

    return {
        "ok": True,
        "look_id": look_id,
        "item_ids": item_ids,
    }
    
@app.post("/upload/ootd-photo")
async def upload_ootd_photo(file: UploadFile = File(...)):
    save_dir = BASE_DIR / "ootd_photos"
    ext      = Path(file.filename).suffix.lower() or ".jpg"
    filename = f"ootd_{uuid.uuid4().hex}{ext}"
    dest     = save_dir / filename
    with open(dest, "wb") as f:
        f.write(await file.read())
    return {"ok": True, "photo_url": str(dest)}


@app.post("/api/looks/{look_id}/tryon")
def trigger_look_tryon(look_id: str, background_tasks: BackgroundTasks):
    look = db.get_look(look_id)
    if not look:
        return {"ok": False, "error": "look not found"}
    if look.get("tryon_url"):
        return {"ok": True, "already_done": True}
    background_tasks.add_task(
        look_manager._generate_tryon_bg,
        look_id, "default", look["item_ids"],
    )
    return {"ok": True}


@app.delete("/api/looks/{look_id}")
def delete_look(look_id: str):
    db.delete_look(look_id)
    return {"ok": True}


@app.get("/looks", response_class=HTMLResponse)
def looks_page(request: Request):
    return templates.TemplateResponse("looks.html", {"request": request})


# ── / 首页 ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ── Tomorrow Planning（每晚 20:00 预生成明日推荐 + 拼图）─────────────────────────
# PRD v1.1 §5/§7：设备空闲时预生成 4–5 套效果图，次日首页直接命中缓存秒开。

TOMORROW_PLANNING_HOUR = 20

def _seconds_until_next(hour: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _run_tomorrow_planning() -> None:
    """同步流程（在 asyncio.to_thread 中调用，避免 image2 阻塞事件循环）。"""
    profile = db.get_user_profile("default") or {}
    user_photo = profile.get("photo_url", "")
    if not user_photo or not Path(user_photo).is_file():
        print("[tomorrow planning] 跳过：用户未上传全身照")
        return
    if not phase1_wardrobe.image2_client.healthz():
        raise ConnectionError("OpenAI API 不可达")

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    weather  = _fetch_weather_for_day(1)
    outfits  = outfit_recommender.precompute_for_date(
        user_id="default", weather=weather, date_iso=tomorrow, n=4,
    )
    if not outfits:
        print("[tomorrow planning] 跳过：衣橱无可用单品")
        return

    key = outfit_recommender.cache_key("default", tomorrow, weather["temp_c"])
    if outfit_recommender.is_generating(key):
        print("[tomorrow planning] 跳过：已在生图")
        return

    outfit_recommender.mark_generating(key, True)
    try:
        print(f"[tomorrow planning] 开始生图，明日天气 {weather['temp_c']}°C {weather.get('description','')}")
        local_paths = outfit_generator.generate_outfit_grid(
            user_photo=user_photo, outfits=outfits,
        )
        outfit_recommender.set_cached_images(key, local_paths)
        print(f"[tomorrow planning] 完成，{len(local_paths)} 张已就绪")
    except Exception as e:
        print(f"[tomorrow planning] 生图失败: {e}")
        raise
    finally:
        outfit_recommender.mark_generating(key, False)


_TOMORROW_MAX_RETRIES  = 3
_TOMORROW_RETRY_DELAY  = 15 * 60  # 每次重试间隔 15 分钟

async def _tomorrow_planning_loop() -> None:
    while True:
        delay = _seconds_until_next(TOMORROW_PLANNING_HOUR)
        print(f"[tomorrow planning] 下次触发: {int(delay)}s 后（约 {int(delay/3600)}h）")
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        for attempt in range(1, _TOMORROW_MAX_RETRIES + 1):
            try:
                await asyncio.to_thread(_run_tomorrow_planning)
                break  # 成功，结束重试
            except asyncio.CancelledError:
                return
            except Exception as e:
                if attempt < _TOMORROW_MAX_RETRIES:
                    print(f"[tomorrow planning] 第 {attempt} 次失败，{_TOMORROW_RETRY_DELAY // 60} 分钟后重试: {e}")
                    await asyncio.sleep(_TOMORROW_RETRY_DELAY)
                else:
                    print(f"[tomorrow planning] 已重试 {attempt} 次，放弃，明日再试: {e}")


@app.on_event("startup")
async def _start_scheduler() -> None:
    asyncio.create_task(_tomorrow_planning_loop())
    _start_tryon_backfill()


def _start_tryon_backfill() -> None:
    """启动时为所有缺失 tryon_url 的 look 串行补生成上身图。"""
    import threading

    def _run():
        looks = db.get_looks(user_id="default", limit=500)
        pending = [lk for lk in looks if not lk.get("tryon_url") and lk.get("item_ids")]
        if not pending:
            return
        print(f"[startup] 补生成 {len(pending)} 条 look 的上身图（串行）...")
        for lk in pending:
            look_manager._generate_tryon_bg(lk["look_id"], "default", lk["item_ids"])

    threading.Thread(target=_run, daemon=True).start()
