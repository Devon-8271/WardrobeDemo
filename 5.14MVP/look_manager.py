"""
look_manager.py
穿搭日志的存取接口。
"""

import os
import uuid
import threading
from datetime import date
import db


def _find_existing_tryon(user_id: str, item_ids: list) -> str:
    """查找已有 look 中相同单品组合的试穿图，返回路径；未找到返回空串。"""
    target = frozenset(item_ids)
    for lk in db.get_looks(user_id=user_id, limit=500):
        if frozenset(lk.get("item_ids", [])) == target and lk.get("tryon_url"):
            return lk["tryon_url"]
    return ""


def _generate_tryon_bg(look_id: str, user_id: str, item_ids: list) -> None:
    """后台线程：调 image2 生成试穿效果图并写回 DB。"""
    try:
        # 相同单品组合已有试穿图则直接复用，不再调 image2
        existing = _find_existing_tryon(user_id, item_ids)
        if existing:
            db.update_look_tryon_url(look_id, existing)
            return

        import image2_client
        if not image2_client.healthz():
            return

        profile = db.get_user_profile(user_id) or {}
        user_photo = profile.get("photo_url", "")
        if not user_photo or not os.path.isfile(user_photo):
            return

        items = [db.get_wardrobe_item(iid) for iid in item_ids]
        items = [it for it in items if it]
        if not items:
            return

        item_images = [it["image_url"] for it in items if os.path.isfile(it.get("image_url", ""))]

        import tryon_skill
        result_path = tryon_skill.run(
            person_photo=user_photo,
            item_images=item_images,
            items=items,
        )
        db.update_look_tryon_url(look_id, result_path)
    except Exception as e:
        print(f"[look_manager] tryon 生成失败 look_id={look_id}: {e}")


def save_look(
    user_id: str,
    item_ids: list,
    photo_url: str = None,
    scene: str = None,
    source: str = "manual",
    look_date: str = None,
) -> str:
    target_date = look_date or date.today().isoformat()
    # 同一天相同单品组合不重复写入
    existing = db.get_looks(user_id=user_id, limit=50)
    key = frozenset(item_ids)
    for lk in existing:
        if lk.get("date") == target_date and frozenset(lk.get("item_ids") or []) == key:
            return lk["look_id"]

    look_id = uuid.uuid4().hex
    db.insert_look({
        "look_id":   look_id,
        "date":      target_date,
        "item_ids":  item_ids,
        "photo_url": photo_url or "",
        "scene":     scene or "",
        "source":    source,
        "user_id":   user_id,
        "tryon_url": "",
    })
    # 每次保存后静默更新风格档案
    try:
        from style_identity import compute_style_identity
        result = compute_style_identity(user_id)
        db.update_style_tags(user_id, result["tags"])
    except Exception:
        pass
    # 后台生成试穿图（不阻塞保存流程）
    threading.Thread(
        target=_generate_tryon_bg,
        args=(look_id, user_id, list(item_ids)),
        daemon=True,
    ).start()
    return look_id


def get_looks(
    user_id: str,
    scene: str = None,
    limit: int = 30,
) -> list:
    return db.get_looks(user_id=user_id, scene=scene, limit=limit)
