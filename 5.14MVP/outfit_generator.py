"""
outfit_generator.py
调用 image2 生成多套穿搭拼图，裁切为独立效果图。
"""

import os
import json
import uuid
from PIL import Image

import db
import tryon_skill

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "prompt_versions")
_OUT_DIR    = "images/grid"


def _load_config(version: str = "grid_v1") -> dict:
    path = os.path.join(_CONFIG_DIR, f"{version}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)



def _crop_grid(grid_path: str, cols: int, rows: int) -> list:
    """将拼图按 cols×rows 均分裁切，返回各格临时文件路径列表。"""
    img = Image.open(grid_path)
    w, h = img.size
    cell_w, cell_h = w // cols, h // rows

    os.makedirs(_OUT_DIR, exist_ok=True)
    paths = []
    for row in range(rows):
        for col in range(cols):
            box  = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
            cell = img.crop(box)
            out  = os.path.join(_OUT_DIR, f"cell_{uuid.uuid4().hex}.png")
            cell.save(out)
            paths.append(out)
    return paths


# ── 公开接口 ───────────────────────────────────────────────────────────────────

def generate_outfit_grid(
    user_photo: str,
    outfits: list,
    config_version: str = "grid_v1",
    occasion: str = None,
    is_weekend: bool = None,
) -> list:
    """
    一次 image2 调用生成多套穿搭拼图，裁切后返回各套效果图路径。
    prompt 构建 + pose_engine + scene_engine 统一由 tryon_skill.run_grid() 负责。
    """
    config = _load_config(config_version)
    cols, rows = config["cols"], config["rows"]
    n = min(len(outfits), cols * rows)

    all_ids  = {iid for o in outfits for iid in o["item_ids"]}
    wardrobe = {iid: db.get_wardrobe_item(iid) for iid in all_ids}

    grid_path = tryon_skill.run_grid(
        user_photo=user_photo,
        outfits=outfits[:n],
        wardrobe=wardrobe,
        cols=cols,
        rows=rows,
        occasion=occasion,
        is_weekend=is_weekend,
    )
    return _crop_grid(grid_path, cols, rows)[:n]


def regenerate_single_outfit(
    user_photo: str,
    item_ids: list,
) -> str:
    """换单品后重新生成单套效果图，返回本地路径。"""
    items = [db.get_wardrobe_item(iid) for iid in item_ids if db.get_wardrobe_item(iid)]
    item_images = [it["image_url"] for it in items if os.path.isfile(it.get("image_url", ""))]
    return tryon_skill.run(
        person_photo=user_photo,
        item_images=item_images,
        items=items,
    )
