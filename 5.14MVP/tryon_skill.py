"""
tryon_skill.py — A + B = C 试穿 Skill

    A : 人物全身照
    B : 白底衣服图（1张=单件，多张=套装）
    C : 试穿效果图

内部自动：
  - pose_engine 根据单品/套装标签推断姿势建议
  - 场景自动选择（室外/室内/棚拍）
  - prompt 构建

被复用的地方：
  - phase2_tryon.virtual_tryon()   单件试穿（CLI/Web 上传）
  - app._run_tryon_outfit()        推荐套装试穿（Hub Action / REST）
  - app._run_quick_tryon()         随手试穿（无 item 元数据）
"""

import os

import image2_client
from pose_engine import build_pose_hint
import scene_engine

_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "images")


def _build_prompt(
    items: list,
    fit_hint: str = "",
    styling_hint: str = "",
    pose_hint: str = "",
    color_override: str = "",
    scene_desc: str = "",
) -> str:
    scene = scene_desc or scene_engine.pick_scene(items)

    if not items:
        clothing_block = "【服装】还原参考图中的衣物"
    elif len(items) == 1:
        primary = items[0]
        color_str = color_override or "、".join(primary.get("color") or [])
        desc = primary.get("description") or primary.get("type", "")
        clothing_block = f"【服装】{desc}"
        detail = f"颜色：{color_str}  类型：{primary.get('type', '')}"
        if fit_hint:
            detail += f"  版型：{fit_hint}"
        clothing_block += f"\n{detail}"
        if styling_hint:
            clothing_block += f"\n穿法：{styling_hint}"
    else:
        types  = "、".join(it.get("type", "") for it in items if it.get("type"))
        colors = color_override or "、".join(
            c for it in items for c in (it.get("color") or [])
        )
        clothing_block = f"【服装】参考图片中的完整套装，包含：{types}"
        if colors:
            clothing_block += f"\n主色调：{colors}"
        if fit_hint:
            clothing_block += f"\n版型：{fit_hint}"
        if styling_hint:
            clothing_block += f"\n穿法：{styling_hint}"

    has_full_body = any(it.get("category") == "全身" for it in items)
    if items and has_full_body:
        part_hint = "【换装范围】参考图为全身款，请补全完整穿搭（上下身均替换为整体协调效果）"
    elif items:
        cats = "、".join(dict.fromkeys(it.get("category", "") for it in items if it.get("category")))
        part_hint = f"【换装范围】只替换参考图中的{cats}，其余部位（颜色 / 款式 / 配件）严格保持图1原样不变"
    else:
        part_hint = ""

    lines = [
        "【任务】将图1中的人物试穿后续图片中的衣物，生成完整试穿效果图。",
        "",
        clothing_block,
        "",
    ]
    if part_hint:
        lines += [part_hint, ""]
    lines += [
        "【人物要求 — 严格保持不变】",
        "面部特征 / 发型 / 发色 / 肤色 / 体型 / 肩宽 / 腿长比例",
        "全身完整入镜，四肢不截断",
    ]

    if pose_hint:
        lines += ["", "【姿势建议】", pose_hint]
    else:
        lines.append("姿态保持与原照片一致")

    lines += [
        "",
        "【服装要求】严格还原图片中衣物的颜色 / 版型 / 面料质感 / 印花细节",
        f"【背景】{scene}，无文字 / logo / 水印",
        "【风格】真实试穿效果图",
    ]
    return "\n".join(lines)


def _build_grid_prompt(outfits_data: list, cols: int, rows: int, scene_desc: str = "") -> str:
    """
    outfits_data: list of {"items": [...], "pose_hint": str}
    每格独立描述单品 + 姿势 + 背景（按各格自身风格决定）。
    """
    n = len(outfits_data)
    lines = [
        f"【任务】将图1中的人物试穿后续图片中的服装，生成 {cols}×{rows} 拼图，共 {n} 格，每格一套完整穿搭。",
        "",
        "【人物】四格为同一人，严格保持：面部特征 / 发型 / 肤色 / 体型 / 肩宽 / 腿长；全身入镜，四肢不截断",
        "",
    ]

    for i, od in enumerate(outfits_data, 1):
        items     = od["items"]
        pose_hint = od.get("pose_hint", "")
        types  = "、".join(it.get("type", "") for it in items if it.get("type"))
        colors = "、".join(c for it in items for c in (it.get("color") or []))

        cell_scene = od.get("scene_desc", "现代都市场景")

        cell = f"【第{i}格】{types}"
        if colors:
            cell += f"  颜色：{colors}"
        if pose_hint:
            cell += f"\n  姿势：{pose_hint}"
        cell += f"\n  背景：{cell_scene}"
        lines.append(cell)

    lines += [
        "",
        "【服装要求】严格还原每格图片中对应单品的颜色 / 版型 / 面料质感 / 印花细节",
        "【输出】等大拼图，无边框，无文字，无水印",
    ]
    return "\n".join(lines)


def run_grid(
    user_photo: str,
    outfits: list,
    wardrobe: dict = None,
    cols: int = 2,
    rows: int = 2,
    occasion: str = None,
    is_weekend: bool = None,
) -> str:
    """
    一次 image2 调用生成 cols×rows 穿搭拼图，返回拼图路径（调用方负责裁切）。

    user_photo  人物全身照路径
    outfits     recommend_outfits() 返回列表，每项含 item_ids
    wardrobe    iid → item dict（传入避免重复查 DB；None 时内部自动查）
    occasion    用户输入的场合（透传自 fashion_router），None 时按风格×日期自动选
    is_weekend  None 时由 scene_engine 自动判断
    """
    import db as _db

    n = min(len(outfits), cols * rows)
    outfits = outfits[:n]

    if wardrobe is None:
        all_ids  = {iid for o in outfits for iid in o["item_ids"]}
        wardrobe = {iid: _db.get_wardrobe_item(iid) for iid in all_ids}

    outfits_data = []
    for outfit in outfits:
        items       = [wardrobe.get(iid) for iid in outfit["item_ids"] if wardrobe.get(iid)]
        scene_group = scene_engine.pick_scene_group(items, occasion=occasion, is_weekend=is_weekend)
        pose_hint   = build_pose_hint(items[0] if items else {}, ootd_items=items, scene_group=scene_group)
        outfits_data.append({"items": items, "pose_hint": pose_hint})

    # 每格根据自身风格独立选择场景，通过 exclude_variant_ids 避免四格重复
    used_variant_ids = set()
    for od in outfits_data:
        scene_desc, variant_id = scene_engine.pick_scene_with_variant(
            od["items"],
            occasion=occasion,
            is_weekend=is_weekend,
            exclude_variant_ids=used_variant_ids,
        )
        od["scene_desc"] = scene_desc
        used_variant_ids.add(variant_id)

    prompt = _build_grid_prompt(outfits_data, cols, rows)

    # 用户照片 + 各套单品图（去重，image2 上限 8 张）
    image_paths: list = [user_photo] if os.path.isfile(user_photo) else []
    seen = set(image_paths)
    for od in outfits_data:
        for item in od["items"]:
            img = item.get("image_url", "")
            if img and os.path.isfile(img) and img not in seen:
                image_paths.append(img)
                seen.add(img)
            if len(image_paths) >= 8:
                break
        if len(image_paths) >= 8:
            break

    _grid_dir = os.path.join(_IMAGES_DIR, "grid")
    return image2_client.generate(
        prompt=prompt,
        image_paths=image_paths or None,
        out_dir=_grid_dir,
        prefix="grid",
    )


def run(
    person_photo: str,
    item_images: list,
    items: list = None,
    fit_hint: str = "",
    styling_hint: str = "",
    color_override: str = "",
    occasion: str = None,
    is_weekend: bool = None,
) -> str:
    """
    A + B → C

    person_photo    人物全身照路径
    item_images     白底衣服图路径列表（1张=单件，多张=套装）
    items           衣物元数据列表（用于 pose_engine + prompt；空列表=无 metadata）
    fit_hint        版型描述字符串，如 "修身——穿着合身贴体"（可选）
    styling_hint    穿法描述字符串，如 "敞开穿——外套完全敞开"（可选）
    color_override  覆盖颜色描述，如 "砖红色"（可选）
    occasion        用户输入的场合（可选）
    is_weekend      None 时由 scene_engine 自动判断

    返回试穿效果图本地路径。
    """
    items = items or []

    scene_group = scene_engine.pick_scene_group(items, occasion=occasion, is_weekend=is_weekend)
    pose_hint   = build_pose_hint(items[0] if items else {}, ootd_items=items, scene_group=scene_group)
    scene_desc = scene_engine.pick_scene(items, occasion=occasion, is_weekend=is_weekend)
    prompt = _build_prompt(
        items,
        fit_hint=fit_hint,
        styling_hint=styling_hint,
        pose_hint=pose_hint,
        color_override=color_override,
        scene_desc=scene_desc,
    )

    valid_item_images = [p for p in item_images if p and os.path.isfile(p)]
    image_paths = [person_photo] + valid_item_images[:7]  # image2 上限 8 张

    return image2_client.generate(
        prompt=prompt,
        image_paths=image_paths,
        out_dir=_IMAGES_DIR,
        prefix="tryon",
    )
