"""
OpenAI GPT-Image-2 图像生成客户端。
替换原 image2 本地服务，接口保持完全兼容。
"""
import os
import base64
import uuid
import threading
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── 初始化客户端 / Init client ──────────────────────────────────────────────
_CLIENT = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), timeout=300.0)  # 5分钟超时

# OpenAI images.edit 隐性并发限制约 2-3，超出会静默排队直到超时
# 用信号量把并发控制在 2，第 3 个调用会在此等待而不是被 OpenAI 挂起
_GENERATE_SEM = threading.Semaphore(2)

# 默认生成尺寸 / Default output size
_DEFAULT_SIZE = "1024x1024"


# ── 内部工具 / Internal helpers ────────────────────────────────────────────


def _save_b64(b64_data: str, out_path: str) -> str:
    """把 base64 图片数据写入本地文件，返回路径。"""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(b64_data))
    return out_path


# ── 核心生成逻辑 / Core generation ────────────────────────────────────────

def _generate_text_to_image(prompt: str, out_path: str) -> str:
    """纯文生图 / Text-to-image（无参考图）。"""
    print("  [gpt-image] 文生图模式...")
    resp = _CLIENT.images.generate(
        model="gpt-image-2",
        prompt=prompt,
        size=_DEFAULT_SIZE,
        n=1,
    )
    b64 = resp.data[0].b64_json
    return _save_b64(b64, out_path)


_MAX_SIDE = 1536  # OpenAI 不需要原始分辨率，超出此值等比缩小


def _to_rgba_png(src_path: str) -> str:
    """把任意格式图片转成 RGBA PNG 临时文件（OpenAI images.edit 要求）。"""
    from PIL import Image
    import tempfile
    img = Image.open(src_path).convert("RGBA")
    w, h = img.size
    if max(w, h) > _MAX_SIDE:
        scale = _MAX_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, format="PNG")
    tmp.close()
    return tmp.name


def _generate_image_edit(prompt: str, image_paths: list, out_path: str) -> str:
    """
    图片编辑 / Image edit（有参考图，含试穿场景）。
    image_paths[0] = 主图（用户照片 or 底图）
    image_paths[1:] = 额外参考图（单品图等）
    """
    import os
    print(f"  [gpt-image] 图片编辑模式，参考图 {len(image_paths)} 张...")

    # OpenAI images.edit 要求 RGBA PNG，先统一转换
    converted = [_to_rgba_png(p) for p in image_paths]
    image_files = [open(p, "rb") for p in converted]
    try:
        resp = _CLIENT.images.edit(
            model="gpt-image-2",
            image=image_files,
            prompt=prompt,
            size=_DEFAULT_SIZE,
            n=1,
        )
    finally:
        for f in image_files:
            f.close()
        for p in converted:
            try:
                os.unlink(p)
            except Exception:
                pass

    b64 = resp.data[0].b64_json
    return _save_b64(b64, out_path)


# ── 公开接口 / Public API（与原 image2_client 完全兼容）─────────────────────

def generate(
    prompt: str,
    image_paths: list = None,
    out_dir: str = "images",
    prefix: str = "gen",
) -> str:
    """
    一站式生成，返回本地图片路径。接口与原 image2_client.generate() 完全兼容。

    Args:
        prompt:      生成指令（中文或英文均可）
        image_paths: 参考图列表；试穿时传 [用户照片, 单品图]；为 None 时文生图
        out_dir:     输出目录
        prefix:      文件名前缀（tryon / beautify / outfit 等）
    """
    out_path = os.path.join(out_dir, f"{prefix}_{uuid.uuid4().hex}.png")
    print(f"  [gpt-image] 提交任务，prefix={prefix}...")

    with _GENERATE_SEM:
        if image_paths:
            result = _generate_image_edit(prompt, image_paths, out_path)
        else:
            result = _generate_text_to_image(prompt, out_path)

    print(f"  [gpt-image] 生成完成: {result}")
    return result


_healthz_cache: dict = {"ok": None, "ts": 0.0}
_HEALTHZ_TTL = 60  # 秒

def healthz() -> bool:
    """
    检查 OpenAI API 是否可用。结果缓存 60s，避免频繁调用阻塞主线程。
    """
    import time
    now = time.time()
    if _healthz_cache["ok"] is not None and now - _healthz_cache["ts"] < _HEALTHZ_TTL:
        return _healthz_cache["ok"]
    try:
        _CLIENT.models.retrieve("gpt-image-2")
        _healthz_cache.update({"ok": True, "ts": now})
        return True
    except Exception:
        _healthz_cache.update({"ok": False, "ts": now})
        return False