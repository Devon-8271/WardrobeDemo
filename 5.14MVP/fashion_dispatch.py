"""
fashion_dispatch.py
Fashion Skill 统一调度层：接收用户输入 → 路由 → 调各模块 → 返回 AgentResponse。

返回格式（对齐 AI_Hub_Skill_Protocol_v0.1 Section 2.2）：
  {
    "skill":          "fashion",
    "action":         str,
    "message":        str,
    "cards":          list[HubCard],
    "context_update": SkillContextPatch | None,
    "error":          HubError | None,
  }
"""

import db
import fashion_router
import outfit_recommender
import outfit_generator
import look_manager


def dispatch(
    user_input: str,
    user_id: str = "default",
    context: dict = None,
    weather: dict = None,
    dry_run: bool = False,
) -> dict:
    """
    主入口。

    context 可含（新协议格式）：
      current_entity_id   str               当前焦点 outfit 的 entity_id
      candidate_entity_ids list[str]         候选 entity_id 列表
      entities            dict[str, entity] entity_id → SkillEntity
    weather: {"temp_c": float, "description": str}
    """
    context = context or {}
    weather = weather or {"temp_c": 20, "description": "晴"}

    route = fashion_router.route(user_input)
    key   = route["key"]

    if key == "recommend":
        return _handle_recommend(user_id, weather, context, dry_run, user_input)

    if key == "swap_item":
        # 从 context.entities 取当前套装 item_ids
        item_ids = _current_item_ids(context)
        if item_ids:
            return _handle_swap_item(user_id, item_ids, context, route, weather)
        return _handle_recommend(user_id, weather, context, dry_run, user_input)

    if key == "wardrobe_query":
        return _handle_wardrobe_query(user_id, route)

    if key == "save_look":
        return _handle_save_look(user_id, context, look_date=context.get("save_date"))

    if key == "quick_tryon":
        return _resp("quick_tryon", "请发送想试穿的服装图片，我来帮你上身效果预览。")

    return _resp("unknown", "我没太明白你的意思，可以告诉我想穿什么场合，或者让我帮你推荐今天的搭配吗？")


# ── 工具函数 ────────────────────────────────────────────────────────────────────

def _resp(action: str, message: str, cards=None, context_update=None, error=None) -> dict:
    """构建标准 AgentResponse。"""
    return {
        "skill":          "fashion",
        "action":         action,
        "message":        message,
        "cards":          cards or [],
        "context_update": context_update,
        "error":          error,
    }


def _current_item_ids(context: dict) -> list:
    """从 context.entities 取当前 outfit 的 item_ids，兼容旧 current_item_ids。"""
    entity_id = context.get("current_entity_id")
    if entity_id:
        entity = context.get("entities", {}).get(entity_id, {})
        ids = entity.get("metadata", {}).get("item_ids")
        if ids:
            return ids
    return context.get("current_item_ids", [])


def _current_entity_id(context: dict) -> str:
    return context.get("current_entity_id", "OUTFIT_000")


def _get_user_photo(user_id: str, context: dict) -> str:
    if context.get("user_photo"):
        return context["user_photo"]
    profile = db.get_user_profile(user_id)
    return profile.get("photo_url", "") if profile else ""


# ── 各路由处理 ──────────────────────────────────────────────────────────────────

def _handle_recommend(user_id, weather, context, dry_run, user_input: str = ""):
    occasion = user_input.strip() or None
    outfits = outfit_recommender.recommend_outfits(
        user_id=user_id,
        weather=weather,
        occasion=occasion,
    )
    if not outfits:
        return _resp(
            "recommend",
            "衣橱单品不足，暂时无法生成搭配，快去上传更多单品吧！",
            error={"code": "NO_WARDROBE", "message": "衣橱单品不足，暂时无法生成搭配，快去上传更多单品吧！"},
        )

    images = []
    if not dry_run:
        user_photo = _get_user_photo(user_id, context)
        if user_photo:
            try:
                images = outfit_generator.generate_outfit_grid(
                    user_photo=user_photo,
                    outfits=outfits,
                )
            except Exception:
                images = []

    entity_ids = [f"OUTFIT_{i:03d}" for i in range(len(outfits))]
    temp_c = weather.get("temp_c", 20)
    desc   = weather.get("description", "")

    card_items = [
        {
            "id":      entity_ids[i],
            "title":   outfit.get("caption") or "搭配推荐",
            "image":   images[i] if i < len(images) else "",
            "caption": outfit.get("caption", ""),
            "tags":    outfit.get("style_tags", [])[:3],
            "actions": [
                {"label": "试穿",   "event": "try_on",    "params": {"entity_id": entity_ids[i]}, "style": "primary"},
                {"label": "换一件", "event": "swap_item", "params": {"entity_id": entity_ids[i]}, "style": "secondary"},
                {"label": "保存",   "event": "save_look", "params": {"entity_id": entity_ids[i]}, "style": "ghost"},
            ],
            "metadata": {"item_ids": outfit["item_ids"]},
        }
        for i, outfit in enumerate(outfits)
    ]

    card = {
        "id":       "CARD_RECOMMEND",
        "type":     "outfit_recommendation",
        "skill":    "fashion",
        "title":    "今日穿搭推荐",
        "subtitle": f"{desc} · {temp_c}°C" if desc else f"{temp_c}°C",
        "display":  "horizontal_carousel",
        "items":    card_items,
    }

    entities = {
        entity_ids[i]: {
            "id":       entity_ids[i],
            "type":     "outfit",
            "title":    outfit.get("caption", ""),
            "metadata": {"item_ids": outfit["item_ids"]},
        }
        for i, outfit in enumerate(outfits)
    }

    return _resp(
        "recommend",
        outfits[0].get("caption") or "为你推荐今日搭配",
        cards=[card],
        context_update={
            "current_entity_id":    entity_ids[0],
            "candidate_entity_ids": entity_ids,
            "entities":             entities,
        },
    )


def _handle_swap_item(user_id, item_ids, context, route, weather, entity_id=None):
    """
    在当前 outfit 上替换一件目标品类的单品。
    秒返单品元数据，不调 image2。
    """
    import random
    from outfit_recommender import _allowed_warmth

    entity_id  = entity_id or _current_entity_id(context)
    target_cat = route.get("category")

    if not target_cat:
        return _resp("swap_item", "想换哪类？比如「换条裤子」「换件上衣」「换件外套」。")

    current_items = [db.get_wardrobe_item(iid) for iid in item_ids]
    current_items = [it for it in current_items if it]
    to_remove     = next((it for it in current_items if it.get("category") == target_cat), None)

    allowed    = _allowed_warmth(weather.get("temp_c", 15), user_id)
    all_items  = db.get_all_wardrobe_items(source_filter="real")
    candidates = [
        it for it in all_items
        if it.get("category") == target_cat
        and (not to_remove or it["item_id"] != to_remove["item_id"])
        and (it.get("warmth") or "不适用") in allowed
    ]

    if not candidates:
        msg = (
            f"你的衣橱里暂时只有这件{target_cat}「{to_remove.get('type','')}」，去添加一件新的吧。"
            if to_remove else
            f"衣橱里还没有{target_cat}，先添加一件吧。"
        )
        return _resp(
            "swap_item", msg,
            error={"code": "NO_CANDIDATES", "message": msg, "retryable": False},
        )

    new_item = random.choice(candidates)
    new_ids  = (
        [new_item["item_id"] if iid == to_remove["item_id"] else iid for iid in item_ids]
        if to_remove else list(item_ids) + [new_item["item_id"]]
    )

    card_item = {
        "id":      entity_id,
        "title":   "更新版搭配",
        "caption": f"换成了「{new_item.get('type','')}」",
        "actions": [
            {"label": "保存", "event": "save_look", "params": {"entity_id": entity_id}, "style": "primary"},
        ],
        "metadata": {
            "item_ids":   new_ids,
            "swapped_to": {"item_id": new_item["item_id"], "type": new_item.get("type", "")},
            "available":  len(candidates),
        },
    }

    return _resp(
        "swap_item",
        f"换成了「{new_item.get('type','')}」，看看搭配效果如何？",
        cards=[{
            "id":      "CARD_SWAP",
            "type":    "outfit_update",
            "skill":   "fashion",
            "title":   "已更新搭配",
            "display": "single",
            "items":   [card_item],
        }],
        context_update={
            "current_entity_id": entity_id,
            "entities": {
                entity_id: {
                    "id":       entity_id,
                    "type":     "outfit",
                    "metadata": {"item_ids": new_ids},
                }
            },
        },
    )


def _handle_wardrobe_query(user_id, route):
    items    = db.get_all_wardrobe_items(source_filter="real")
    category = route.get("category")
    if category:
        items = [it for it in items if it.get("category") == category]

    card_items = [
        {
            "id":    it["item_id"],
            "title": it.get("type", ""),
            "image": it.get("image_crop_url") or it.get("image_url", ""),
            "tags":  (it.get("color") or [])[:1],
            "metadata": {"item_id": it["item_id"]},
        }
        for it in items[:20]
    ]

    return _resp(
        "wardrobe_query",
        f"你的衣橱共有 {len(items)} 件单品。",
        cards=[{
            "id":      "CARD_WARDROBE",
            "type":    "wardrobe_list",
            "skill":   "fashion",
            "title":   f"衣橱 · {len(items)} 件",
            "display": "grid",
            "items":   card_items,
        }] if items else [],
    )


def _handle_save_look(user_id, context, look_date=None):
    item_ids = _current_item_ids(context)
    if not item_ids:
        return _resp(
            "save_look",
            "请先选择一套搭配，再保存哦。",
            error={"code": "NO_ACTIVE_OUTFIT", "message": "请先选择一套搭配，再保存哦。"},
        )
    photo_url = context.get("current_photo", "")
    look_manager.save_look(
        user_id=user_id,
        item_ids=item_ids,
        photo_url=photo_url or None,
        source="manual",
        look_date=look_date or None,
    )
    return _resp("save_look", "搭配已保存到你的穿搭日志！")


def _handle_try_on(user_id: str, entity_id: str, context: dict) -> dict:
    """验证试穿前置条件；通过后信号 app.py 创建后台任务。"""
    import image2_client

    entity   = context.get("entities", {}).get(entity_id, {})
    item_ids = entity.get("metadata", {}).get("item_ids", [])
    if not item_ids:
        return _resp(
            "try_on", "",
            error={"code": "NO_OUTFIT", "message": "没有找到当前搭配，请先推荐一套。"},
        )

    user_photo = _get_user_photo(user_id, context)
    if not user_photo:
        return _resp(
            "try_on", "",
            error={"code": "NO_USER_PHOTO", "message": "请先上传一张全身照。", "retryable": False},
        )

    if not image2_client.healthz():
        return _resp(
            "try_on", "",
            error={"code": "IMAGE_SERVICE_OFFLINE", "message": "图像服务暂不可达，请检查 Wi-Fi。", "retryable": True},
        )

    # 把任务参数通过私有字段传给 app.py，由 app.py 负责创建 BG task
    return _resp(
        "try_on",
        "正在生成试穿图，需要 1-2 分钟。",
        context_update={
            "_try_on_item_ids":   item_ids,
            "_try_on_user_photo": user_photo,
        },
    )


# ── Hub Action 路由入口 ─────────────────────────────────────────────────────────

def handle_action(event: str, params: dict, user_id: str, context: dict) -> dict:
    """Hub 按钮事件路由（try_on / swap_item / save_look）。"""
    entity_id = params.get("entity_id", "")

    if event == "try_on":
        return _handle_try_on(user_id, entity_id, context)

    if event == "swap_item":
        item_ids = _current_item_ids(context)
        route    = {"category": params.get("category", "")}
        weather  = params.get("weather") or {"temp_c": 20, "description": "晴"}
        if item_ids:
            return _handle_swap_item(user_id, item_ids, context, route, weather, entity_id)
        return _resp(
            "swap_item", "没有当前搭配，请先推荐一套。",
            error={"code": "NO_ACTIVE_OUTFIT", "message": "没有当前搭配，请先推荐一套。"},
        )

    if event == "save_look":
        return _handle_save_look(user_id, context, look_date=params.get("date"))

    return _resp("unknown_action", f"未知操作：{event}")
