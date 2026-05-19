"""
mcp_label.py — NAS 批量白底图生成 + 打标，生成 items.json

前提：
  - NAS 挂载在 /Volumes/home/，MCP server 通过 nas:// 协议直接访问
  - 本地路径 /Volumes/home/数据采集/帽子/x.jpg → nas://数据采集/帽子/x.jpg
  - 2000 张均为单件单品

流程（每张图）：
  Step 1  generate_item_image(nas://...)  → 白底图 URL
  Step 2  extract_item_tags(白底图 url)   → 结构化标签
  输出    items.json（兼容 import_batch.py）

用法：
  python mcp_label.py <本地图片根目录> [--workers N] [--out items.json]

示例：
  python mcp_label.py /Volumes/home/数据采集 --workers 3

断点续跑：mcp_label_progress.json（key=nas:// URL）
失败记录：mcp_label_skip.json
"""

import os
import sys
import json
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

MCP_SERVER    = "http://192.168.31.113:9001/mcp"
NAS_MOUNT     = "/Volumes/home"
IMG_EXTS      = {".jpg", ".jpeg", ".png", ".webp"}
PROGRESS_FILE = "mcp_label_progress.json"
SKIP_FILE     = "mcp_label_skip.json"

FOLDER_CATEGORY = {
    "帽子":                   "配件",
    "发饰":                   "配件",
    "围巾":                   "配件",
    "项链":                   "配件",
    "手链":                   "配件",
    "耳环":                   "配件",
    "眼镜":                   "配件",
    "腰带":                   "配件",
    "包包":                   "配件",
    "鞋子":                   "鞋履",
    "手持衣服照片":             "上装",
    "手持平铺拍摄方式衣服照片":   "上装",
}


def to_nas_url(local_path: str) -> str:
    rel = Path(local_path).relative_to(NAS_MOUNT)
    return "nas://" + str(rel).replace(os.sep, "/")


def mcp_call(tool: str, arguments: dict, timeout: int = 120) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    r = requests.post(MCP_SERVER, json=payload, timeout=timeout)
    r.raise_for_status()
    result = r.json()
    text = result.get("result", {}).get("content", [{}])[0].get("text", "{}")
    return json.loads(text)


def process_one(nas_url: str, folder_hint: str = "") -> dict:
    gen = mcp_call("generate_item_image", {"image_url": nas_url}, timeout=180)
    if not gen.get("ok"):
        raise RuntimeError(f"generate_item_image failed: {gen}")
    white_bg_url = gen["image_url"]

    tags = mcp_call("extract_item_tags", {"image_url": white_bg_url})
    if not tags.get("ok"):
        raise RuntimeError(f"extract_item_tags failed: {tags}")
    fields = tags["fields"]

    category = fields.get("category") or FOLDER_CATEGORY.get(folder_hint, "")

    return {
        "category":    category,
        "type":        fields.get("type",        ""),
        "raw_type":    fields.get("raw_type",    ""),
        "color":       fields.get("color",       []),
        "style":       fields.get("style",       []),
        "season":      fields.get("season",      []),
        "warmth":      fields.get("warmth",      "无法判断"),
        "fit":         fields.get("fit",         "无法判断"),
        "description": fields.get("description", ""),
        "image":       white_bg_url,
        "_source":     nas_url,
        "_folder":     folder_hint,
    }


def load_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image_dir",           help="本地图片根目录（如 /Volumes/home/数据采集）")
    parser.add_argument("--workers", type=int, default=3,           help="并发数（默认 3）")
    parser.add_argument("--limit",   type=int, default=0,           help="只处理前 N 张，0=全部（默认 0）")
    parser.add_argument("--out",               default="items.json", help="输出文件（默认 items.json）")
    args = parser.parse_args()

    root = Path(args.image_dir)
    if not root.is_dir():
        print(f"❌ 目录不存在：{root}")
        sys.exit(1)

    tasks = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in IMG_EXTS and p.is_file():
            nas_url = to_nas_url(str(p))
            folder  = p.relative_to(root).parts[0] if p.relative_to(root).parts else ""
            tasks.append((nas_url, folder))

    print(f"共扫描到 {len(tasks)} 张图片")

    progress: dict = load_json(PROGRESS_FILE)
    skip:     dict = load_json(SKIP_FILE)
    todo = [(u, f) for u, f in tasks if u not in progress and u not in skip]
    if args.limit > 0:
        todo = todo[:args.limit]
    print(f"待处理：{len(todo)} 张（已完成 {len(progress)}，跳过 {len(skip)}）\n")

    lock = threading.Lock()

    def worker(nas_url: str, folder: str):
        name = nas_url.split("/")[-1]
        try:
            item = process_one(nas_url, folder_hint=folder)
            with lock:
                progress[nas_url] = item
                save_json(PROGRESS_FILE, progress)
            print(f"  ✓ [{folder}] {name} → {item['category']} / {item['raw_type']}")
        except Exception as e:
            with lock:
                skip[nas_url] = str(e)
                save_json(SKIP_FILE, skip)
            print(f"  ✗ [{folder}] {name} — {str(e)[:100]}")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, u, f) for u, f in todo]
        for _ in as_completed(futures):
            pass

    all_items = [v for v in progress.values() if v]
    save_json(args.out, all_items)
    print(f"\n{'='*55}")
    print(f"完成：{len(all_items)} 条写入 {args.out}，失败：{len(skip)} 张（见 {SKIP_FILE}）")
    print(f"下一步：python import_batch.py {args.out}")


if __name__ == "__main__":
    main()
