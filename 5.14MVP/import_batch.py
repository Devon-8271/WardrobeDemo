"""
import_batch.py
将同事交付的 items.json + images/ 批量导入衣橱数据库。

用法：
  python import_batch.py <items.json 路径>

示例：
  python import_batch.py /path/to/delivery/items.json
  python import_batch.py ./sample_items.json
"""

import os
import sys
import json
import uuid
import shutil
from datetime import datetime

from db import init_db, insert_wardrobe_item, get_all_wardrobe_items

IMAGES_DIR = "images"

VALID_CATEGORY = {"上装", "下装", "全身", "外套", "鞋履", "配件"}
VALID_WARMTH   = {"薄", "中等", "厚", "不适用", "无法判断", ""}
VALID_FIT      = {"修身", "常规", "宽松", "oversize", "不适用", "无法判断", ""}


def _validate(item: dict, idx: int) -> list[str]:
    errors = []
    if not item.get("category") or item["category"] not in VALID_CATEGORY:
        errors.append(f"[{idx}] category 无效：{item.get('category')!r}")
    if not item.get("type"):
        errors.append(f"[{idx}] type 缺失")
    if not item.get("color"):
        errors.append(f"[{idx}] color 缺失")
    if item.get("warmth", "") not in VALID_WARMTH:
        errors.append(f"[{idx}] warmth 无效：{item.get('warmth')!r}")
    if item.get("fit", "") not in VALID_FIT:
        errors.append(f"[{idx}] fit 无效：{item.get('fit')!r}")
    return errors


def _copy_image(src_path: str, item_id: str) -> str:
    """复制图片到本地 images/ 目录，返回存储路径。"""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    ext = src_path.rsplit(".", 1)[-1].lower() if "." in src_path else "jpg"
    dst = os.path.join(IMAGES_DIR, f"{item_id}.{ext}")
    shutil.copy2(src_path, dst)
    return dst


def run(json_path: str):
    if not os.path.isfile(json_path):
        print(f"❌ 找不到文件：{json_path}")
        sys.exit(1)

    # 推断图片源目录：items.json 同级的 images/ 文件夹
    source_dir = os.path.dirname(os.path.abspath(json_path))
    source_images_dir = os.path.join(source_dir, "images")

    with open(json_path, encoding="utf-8") as f:
        items = json.load(f)

    if not isinstance(items, list):
        print("❌ items.json 格式错误，应为 JSON 数组")
        sys.exit(1)

    init_db()

    total = len(items)
    ok = 0
    skipped = 0
    errors = []

    print(f"\n开始导入，共 {total} 件...\n")

    for idx, raw in enumerate(items, 1):
        # 校验字段
        field_errors = _validate(raw, idx)
        if field_errors:
            for e in field_errors:
                print(f"  ⚠️  {e}")
            skipped += 1
            continue

        # 处理图片：支持 URL 和本地文件两种来源
        image_field = raw.get("image", "")
        item_id = uuid.uuid4().hex
        image_url = ""
        if image_field.startswith("http://") or image_field.startswith("https://"):
            # 同事 skill 交付：image 字段是 URL，直接存，不下载
            image_url = image_field
        elif image_field:
            # 本地文件：原有逻辑，从 images/ 目录复制
            src_path = os.path.join(source_images_dir, image_field)
            if os.path.isfile(src_path):
                try:
                    image_url = _copy_image(src_path, item_id)
                except Exception as e:
                    print(f"  ⚠️  [{idx}] 图片复制失败：{e}")
                    skipped += 1
                    continue
            else:
                print(f"  ⚠️  [{idx}] 图片不存在：{src_path}，跳过")
                skipped += 1
                continue

        item = {
            "item_id":     item_id,
            "category":    raw.get("category", ""),
            "type":        raw["type"],
            "raw_type":    raw.get("raw_type", raw["type"]),
            "color":       raw.get("color", []),
            "style":       raw.get("style", []),
            "season":      raw.get("season", []),
            "warmth":      raw.get("warmth", ""),
            "fit":         raw.get("fit", ""),
            "description": raw.get("description", ""),
            "image_url":   image_url,
            "source":      "real",
            "upload_time": datetime.now().isoformat(),
        }

        try:
            insert_wardrobe_item(item)
            ok += 1
            print(f"  ✓ [{idx}/{total}] {item['category']} / {item['raw_type']}  {item['description'][:20]}")
        except Exception as e:
            print(f"  ❌ [{idx}] 写入失败：{e}")
            errors.append(idx)
            skipped += 1

    print(f"\n{'='*50}")
    print(f"导入完成：成功 {ok} / 跳过 {skipped} / 失败 {len(errors)}")
    db_total = len(get_all_wardrobe_items())
    print(f"当前数据库共 {db_total} 件衣物")

    if errors:
        print(f"失败序号：{errors}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python import_batch.py <items.json 路径>")
        sys.exit(1)
    run(sys.argv[1])
