"""
auto_label.py  —  批量自动打标，生成 items.json

用法：
  python auto_label.py <图片目录>                  # 扫目录下所有图片，输出 items.json
  python auto_label.py <图片目录> --out result.json # 自定义输出路径
  python auto_label.py <图片目录> --resume          # 断点续跑（默认已开启）
  python auto_label.py <图片目录> --delay 3         # 每次请求间隔秒数（默认 2）

输出文件：
  items.json            ← 打标结果，可直接传 import_batch.py
  auto_label_skip.json  ← 识别失败 / 返回空的图片列表，需人工处理
  auto_label_progress.json  ← 断点文件，中断后继续跑自动跳过已完成项

Groq 免费限速参考：
  llama-4-scout：30 req/min，建议 --delay 2（默认）
  如有 paid 账户可以把 delay 调到 0.5
"""

import os
import sys
import json
import time
import base64
import re
import argparse
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

_VISION_PROMPT = """识别照片中**用户主动展示的主体单品**，严格只返回 JSON 数组，不要任何其他文字、代码块标记或说明。

判断主体的规则（按优先级）：
1. 占画面面积最大、处于画面中心、对焦清晰的衣物
2. 平铺/挂起/手持展示的衣物（明显是被刻意拍摄的）
3. 同款不同色的展示组合（如吊牌/陈列）算多件，分开列出

**必须忽略**：
- 背景货架、衣架上其他陪衬商品
- 模糊、被遮挡过半、只露出小部分的衣物
- 试衣间镜子里反射的其他衣物
- 模特身上穿的（除非整张图就是模特展示）

如果照片里没有清晰可见的主体单品，返回空数组 []。
宁可漏识别，不要把背景杂物当主体。

每个 item 格式（所有字段必填）：
[
  {
    "category": "上装 | 下装 | 全身 | 外套 | 鞋履 | 配件",
    "type": "具体品类，如针织衫/直筒裤/风衣",
    "color": ["主色"],
    "style": ["风格标签，如休闲/法式/通勤"],
    "season": ["适合季节，春/夏/秋/冬"],
    "warmth": "薄 | 中等 | 厚 | 不适用",
    "fit": "修身 | 常规 | 宽松 | oversize | 不适用",
    "description": "15字内描述"
  }
]"""


def _call_groq_vision(image_path: str) -> list | None:
    """
    调 Groq Vision 识别单张图片，返回 item 列表。
    识别失败返回 None，识别为空返回 []。
    """
    try:
        from groq import Groq
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = Path(image_path).suffix.lower().lstrip(".")
        mime = f"image/{'jpeg' if ext == 'jpg' else ext}"

        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        resp = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": _VISION_PROMPT},
            ]}],
            max_tokens=1024,
            temperature=0,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            content = match.group(0)
        return json.loads(content)
    except Exception as e:
        return None  # 调用异常，交给调用方处理


def _load_progress(progress_path: str) -> dict:
    if os.path.isfile(progress_path):
        with open(progress_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_progress(progress_path: str, progress: dict):
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def run(images_dir: str, out_path: str, delay: float, resume: bool):
    images_dir = os.path.abspath(images_dir)
    if not os.path.isdir(images_dir):
        print(f"❌ 目录不存在：{images_dir}")
        sys.exit(1)

    # 收集所有图片，排序保证可重现
    all_images = sorted([
        f for f in os.listdir(images_dir)
        if Path(f).suffix.lower() in SUPPORTED_EXT
    ])
    total = len(all_images)
    if total == 0:
        print("❌ 目录下没有图片")
        sys.exit(1)

    progress_path = os.path.join(os.path.dirname(out_path), "auto_label_progress.json")
    skip_path = os.path.join(os.path.dirname(out_path), "auto_label_skip.json")

    progress = _load_progress(progress_path) if resume else {}
    skipped_files = []

    print(f"\n图片目录：{images_dir}")
    print(f"共 {total} 张图，已完成 {len(progress)} 张（断点续跑）\n")

    done = 0
    for i, filename in enumerate(all_images, 1):
        if filename in progress:
            continue  # 断点跳过

        image_path = os.path.join(images_dir, filename)
        print(f"[{i}/{total}] {filename} ... ", end="", flush=True)

        results = _call_groq_vision(image_path)

        if results is None:
            print("⚠ 调用失败，加入待审核")
            skipped_files.append({"file": filename, "reason": "api_error"})
            progress[filename] = None
        elif len(results) == 0:
            print("⚠ 识别为空，加入待审核")
            skipped_files.append({"file": filename, "reason": "empty_result"})
            progress[filename] = []
        else:
            # 补充 image 字段和 raw_type
            for item in results:
                item["image"] = filename
                item.setdefault("raw_type", item.get("type", ""))
            progress[filename] = results
            print(f"✓ {results[0]['category']} / {results[0].get('description', '')[:15]}")
            done += 1

        # 每 20 张保存一次断点
        if i % 20 == 0:
            _save_progress(progress_path, progress)

        if delay > 0:
            time.sleep(delay)

    # 最终保存断点
    _save_progress(progress_path, progress)

    # 展开所有成功结果到 items 列表
    items = []
    for filename in all_images:
        results = progress.get(filename)
        if results:
            items.extend(results)

    # 写 items.json
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    # 写 skip 列表
    if skipped_files:
        with open(skip_path, "w", encoding="utf-8") as f:
            json.dump(skipped_files, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"完成：{done} 张成功识别，{len(skipped_files)} 张需人工处理")
    print(f"items.json → {out_path}  （共 {len(items)} 件单品）")
    if skipped_files:
        print(f"待审核列表 → {skip_path}")
    print(f"\n下一步：python import_batch.py {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量自动打标，生成 items.json")
    parser.add_argument("images_dir", help="图片目录路径")
    parser.add_argument("--out", default="items.json", help="输出文件路径（默认 items.json）")
    parser.add_argument("--delay", type=float, default=2.0, help="每次请求间隔秒数（默认 2，对应 Groq 30 req/min）")
    parser.add_argument("--no-resume", action="store_true", help="不使用断点，从头开始")
    args = parser.parse_args()

    run(
        images_dir=args.images_dir,
        out_path=args.out,
        delay=args.delay,
        resume=not args.no_resume,
    )
