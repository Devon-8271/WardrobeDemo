"""
两步链路测试：橱窗识别 → 单品列表（含二次追问）→ image2 生图

用法：
  cd 5.14MVP
  python test_store_pipeline.py <人物全身照> <店内图1> [店内图2 ...]
  python test_store_pipeline.py --version v3  ...   # 指定版本号（默认 v4）

示例：
  python test_store_pipeline.py ../橱窗素材/person.jpg ../橱窗素材/IMG_3863.JPG \
      ../橱窗素材/IMG_3864.JPG ../橱窗素材/IMG_3865.JPG \
      ../橱窗素材/IMG_3866.JPG ../橱窗素材/IMG_3867.JPG
"""

import os
import sys
import base64

from dotenv import load_dotenv
load_dotenv()

import image2_client

# ── Step 1a：第一遍扫描 ─────────────────────────────────────────────────────────

_STORE_SCAN_PROMPT = """以下是同一家服装店的多张实拍图。请扫描所有图片，整理出店内**可见的单品清单**。

要求：
- 按品类分组：上装 / 下装 / 外套 / 全身 / 鞋履 / 配件
- 每件单品一行：「品类 · 颜色 · 款式名」（15字以内），如「全身 · 白色 · 蕾丝连衣裙」
- 只列可清晰辨认的单品，模糊叠挂辨认不清的跳过
- 不要解释，不要标序号，直接输出清单

输出格式示例：
上装 · 海军蓝 · 印花T恤
上装 · 蓝色 · 长袖衬衫
下装 · 浅蓝 · 阔腿牛仔裤
外套 · 卡其 · 拼色工装夹克
全身 · 白色 · 蕾丝吊带连衣裙
鞋履 · 红色 · 运动鞋
配件 · 粉色 · 棒球帽
配件 · 米色 · 草帽"""

# ── Step 1b：针对全身装的二次追问 ───────────────────────────────────────────────

_FOLLOWUP_PROMPT = """再看一遍同一批图片。

上一步的清单可能漏掉了**完整连衣裙、套装、连体衣**（这类单品挂在衣架上不易识别）。

请专门检查：图中是否有完整的连衣裙、套装或连体衣？如有，补充到清单里，格式同上：
「全身 · 颜色 · 款式名」

如果上一步清单已经包含了所有全身装，直接回复"无补充"。"""


def _compress_to_b64(image_path: str, max_side: int = 1024) -> str:
    from PIL import Image
    import io
    img = Image.open(image_path).convert("RGB")
    ratio = min(max_side / img.width, max_side / img.height, 1.0)
    if ratio < 1.0:
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _groq_vision(client, content: list, max_tokens: int = 1024) -> str:
    resp = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": content}],
        max_tokens=max_tokens,
        temperature=0,
    )
    return resp.choices[0].message.content.strip()


def extract_items_from_store(store_image_paths: list) -> str:
    """Step 1：第一遍扫描 + 二次追问全身装，合并返回完整清单。"""
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    # 图片 base64（复用，两次调用都用）
    image_blocks = []
    for p in store_image_paths:
        b64 = _compress_to_b64(p)
        image_blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    # Step 1a：主扫描
    print("  Step 1a — 主扫描...")
    catalog_raw = _groq_vision(client, image_blocks + [{"type": "text", "text": _STORE_SCAN_PROMPT}])

    # Step 1b：二次追问全身装
    print("  Step 1b — 追问全身装盲区...")
    followup_content = image_blocks + [
        {"type": "text", "text": f"上一步识别到的清单如下：\n{catalog_raw}\n\n{_FOLLOWUP_PROMPT}"}
    ]
    supplement = _groq_vision(client, followup_content, max_tokens=256)

    # 合并：过滤掉"无补充"类回复
    if supplement and "无补充" not in supplement and len(supplement.strip()) > 5:
        print(f"  → 补充了全身装：{supplement.strip()}")
        return catalog_raw + "\n" + supplement.strip()

    print("  → 无需补充")
    return catalog_raw


# ── Step 2：拼 image2 prompt 并生图 ────────────────────────────────────────────

def build_image2_prompt(item_catalog: str) -> str:
    return (
        "以提供的参考图为基础：首张为目标人物全身照，其余为服装门店实拍。"
        "请为该人物生成 2×2 穿搭效果图，共 4 格，每格一套完整搭配。\n\n"
        "【人物】严格保留首张参考图中的脸部、发型、肤色、体型特征，仅替换服装，四格为同一人。\n"
        f"【可用单品】以下是门店实际在售单品清单，每套搭配只能使用清单内的单品：\n{item_catalog}\n"
        "【搭配要求】上装+下装必选（或选全身装代替），可搭配清单内配件；"
        "四套风格各异：街头休闲、学院通勤、约会出行、活动穿搭。"
        "若某风格所需单品在清单中不存在，跳过该风格，不得自行创作清单外单品。\n"
        "【画面】背景统一为简洁室内浅色环境；四格等大，无边框，无文字，无水印。"
    )


# ── 主流程 ──────────────────────────────────────────────────────────────────────

def run(person_photo: str, store_photos: list, out_dir: str = "../橱窗素材/results", version: str = "v4"):
    print("=" * 60)
    print(f"[{version}] Step 1 — Groq VLM 扫描店内单品（含二次追问）...")
    print(f"  输入：{len(store_photos)} 张店内图")

    item_catalog = extract_items_from_store(store_photos)

    print("\n  完整单品清单：")
    for line in item_catalog.splitlines():
        print(f"    {line}")

    catalog_path = os.path.join(out_dir, f"{version}_catalog.txt")
    os.makedirs(out_dir, exist_ok=True)
    with open(catalog_path, "w") as f:
        f.write(item_catalog)
    print(f"\n  单品清单已保存：{catalog_path}")

    print("\n" + "=" * 60)
    print(f"[{version}] Step 2 — image2 生图（含清单约束 + 禁止创作清单外单品）...")

    if not image2_client.healthz():
        print("  ❌ image2 服务不可达，请检查 Wi-Fi 连接（192.168.31.50:8787）")
        sys.exit(1)

    prompt = build_image2_prompt(item_catalog)
    print(f"\n  Prompt 预览（前200字）：\n  {prompt[:200]}...")

    image_paths = [person_photo] + store_photos[:7]
    result_path = image2_client.generate(
        prompt=prompt,
        image_paths=image_paths,
        out_dir=out_dir,
        prefix=version,
    )

    print(f"\n✅ 完成：{result_path}")
    return result_path


if __name__ == "__main__":
    args = sys.argv[1:]
    version = "v4"

    # 解析 --version 参数
    if "--version" in args:
        idx = args.index("--version")
        version = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    person = args[0]
    stores = args[1:]
    run(person_photo=person, store_photos=stores, version=version)
