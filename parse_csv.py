"""
parse_csv.py  —  wardrobe_items.csv → items.json
用法：python parse_csv.py
输出：docs/items.json（可直接传给 import_batch.py）
"""

import csv
import json
import os

CSV_PATH    = "docs/wardrobe_items.csv"
OUTPUT_PATH = "docs/items.json"
IMAGES_DIR  = os.path.abspath("data_parsein/whites")


def main():
    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    items = []
    skipped = 0

    for row in rows:
        fields_json  = row.get("fields_json", "").strip()
        image_url    = row.get("white_image_url", "").strip()

        if not fields_json or not image_url:
            skipped += 1
            continue

        try:
            fields = json.loads(fields_json)
        except json.JSONDecodeError:
            skipped += 1
            continue

        # 从 URL 末尾提取文件名，拼到本地 data_parsein/whites/
        filename = image_url.split("/")[-1]
        local_path = os.path.join(IMAGES_DIR, filename)

        if not os.path.isfile(local_path):
            print(f"  ⚠️  图片不存在，跳过：{filename}")
            skipped += 1
            continue

        item = {
            "category":    fields.get("category", ""),
            "type":        fields.get("type", ""),
            "raw_type":    fields.get("raw_type", fields.get("type", "")),
            "color":       fields.get("color", []),
            "style":       fields.get("style", []),
            "season":      fields.get("season", []),
            "warmth":      fields.get("warmth", "无法判断"),
            "fit":         fields.get("fit", "无法判断"),
            "description": fields.get("description", ""),
            "image":       local_path,
        }
        items.append(item)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"完成：{len(items)} 件写入 {OUTPUT_PATH}，跳过 {skipped} 行")
    print(f"下一步：cd 5.14MVP && python import_batch.py ../docs/items.json")


if __name__ == "__main__":
    main()
