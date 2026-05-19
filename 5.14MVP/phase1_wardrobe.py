import os
import re
import uuid
import base64
import shutil
import json
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from db import insert_wardrobe_item, get_all_wardrobe_items
import image2_client

IMAGES_DIR = "images"

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

如果照片里没有清晰可见的主体单品，返回空数组 `[]`。
宁可漏识别，不要把背景杂物当主体。

每个 item 格式（所有字段必填）：
[
  {
    "category": "上装 | 下装 | 全身 | 外套 | 鞋履 | 配件",
    "type": "具体品类，如针织衫/直筒裤/风衣",
    "color": ["主色"],
    "style": ["风格标签，描述款式美感，如休闲/法式/通勤/街头/优雅/浪漫/复古/运动/极简/甜美/学院；不要使用性别标签"],
    "season": ["适合季节，春/夏/秋/冬"],
    "warmth": "薄 | 中等 | 厚 | 不适用",
    "fit": "修身 | 常规 | 宽松 | oversize | 不适用",
    "material": ["面料，如棉/真丝/聚酯纤维/牛仔布/针织/麻，无法判断填不适用"],
    "description": "15字内描述"
  }
]"""

_OOTD_PROMPT = """识别照片中人物当前穿着的主要可见单品，严格只返回 JSON 数组，不要任何其他文字、代码块标记或说明。

识别范围：
- 人物身上正在穿的上装、下装、全身、外套、鞋履、配件
- 忽略背景衣物、家具、路人、装饰品
- 如果某件衣物被遮挡严重，无法判断则不要输出
- 宁可漏识别，不要乱识别

每个 item 格式（所有字段必填）：
[
  {
    "category": "上装 | 下装 | 全身 | 外套 | 鞋履 | 配件",
    "type": "具体品类，如针织衫/直筒裤/连衣裙/外套/靴子/项链",
    "color": ["主色"],
    "style": ["风格标签，如休闲/法式/通勤/街头/优雅/浪漫/复古/运动/极简/甜美/学院"],
    "season": ["适合季节，春/夏/秋/冬"],
    "warmth": "薄 | 中等 | 厚 | 不适用",
    "fit": "修身 | 常规 | 宽松 | oversize | 不适用",
    "material": ["面料，无法判断填不适用"],
    "description": "15字内描述"
  }
]"""

def _extract_json_array(content: str) -> list:
    content = content.strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON array found in model output: {content}")
        data = json.loads(match.group(0))

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError(f"Vision output is not a list: {type(data)}")

    return data


def _normalize_item(item: dict) -> dict:
    def as_list(value, default):
        if isinstance(value, list):
            cleaned = [str(x).strip() for x in value if str(x).strip()]
            return cleaned if cleaned else default
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return default

    category = str(item.get("category", "上装")).strip()
    category = category.replace(" ", "")
    allowed_categories = {"上装", "下装", "全身", "外套", "鞋履", "配件"}
    if category not in allowed_categories:
        category = "上装"

    warmth = str(item.get("warmth", "不适用")).strip()
    allowed_warmth = {"薄", "中等", "厚", "不适用"}
    if warmth not in allowed_warmth:
        warmth = "不适用"

    fit = str(item.get("fit", "不适用")).strip()
    allowed_fit = {"修身", "常规", "宽松", "oversize", "不适用"}
    if fit not in allowed_fit:
        fit = "不适用"

    return {
        "category": category,
        "type": str(item.get("type", "未知")).strip() or "未知",
        "color": as_list(item.get("color"), ["无法判断"]),
        "style": as_list(item.get("style"), ["简约"]),
        "season": as_list(item.get("season"), ["春", "秋"]),
        "warmth": warmth,
        "fit": fit,
        "material": as_list(item.get("material"), ["不适用"]),
        "description": str(item.get("description", "")).strip()[:30] or "待确认单品",
    }


def _call_openai_vision(image_path: str, prompt: str = _VISION_PROMPT) -> list:
    """用 OpenAI vision model 识别衣物，失败时降级到 mock。"""
    try:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        ext = image_path.rsplit(".", 1)[-1].lower() if "." in image_path else "jpg"
        if ext in {"jpg", "jpeg"}:
            mime = "image/jpeg"
        elif ext == "png":
            mime = "image/png"
        elif ext == "webp":
            mime = "image/webp"
        else:
            mime = "image/jpeg"

        client = OpenAI(api_key=api_key)

        resp = client.responses.create(
            model=os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini"),
            input=[
                {
                    "role": "user",
                    "content": [
                       {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime};base64,{b64}",
                            "detail": "high",
                        },
                    ],
                }
            ],
        )

        content = resp.output_text.strip()
        data = _extract_json_array(content)
        return [_normalize_item(x) for x in data if isinstance(x, dict)]

    except Exception as e:
        print(f"  [vision] OpenAI 识别失败（{e}），降级到 mock")
        return _mock_vision(image_path)


def _mock_vision(image_path: str) -> list:
    print(f"  [MOCK] 识别图片: {image_path}")
    return [
        {
            "category": "上装",
            "type": "上衣",
            "color": ["白色"],
            "style": ["休闲"],
            "season": ["春", "夏"],
            "warmth": "薄",
            "fit": "宽松",
            "material": ["棉"],
            "description": "简约白色圆领T恤",
        },
    ]


def recognize_clothing(image_path: str):
    """识别衣物，返回结构化属性列表。识别失败时返回 None。"""
    results = _call_openai_vision(image_path)

    if not results or not isinstance(results, list):
        print("  识别结果为空，请重新上传更清晰的照片。")
        return None

    valid = [r for r in results if r.get("description") and r.get("type") != "未知"]
    if not valid:
        print("  识别结果置信度低，请重新上传更清晰的照片。")
        return None

    return valid

def recognize_ootd_items(image_path: str):
    """识别 OOTD 照片中人物身上的穿搭单品。"""
    results = _call_openai_vision(image_path, prompt=_OOTD_PROMPT)

    if not results or not isinstance(results, list):
        print("  OOTD 识别结果为空。")
        return []

    valid = [r for r in results if r.get("description") and r.get("type") != "未知"]
    return valid


def _edit_style_tags(tags: list) -> list:
    """让用户勾选保留哪些 style tags，可追加自定义标签。"""
    print("  风格标签（AI 生成，请确认）：")
    for i, tag in enumerate(tags, 1):
        print(f"    {i}. {tag}")

    raw = input("  输入要保留的编号（空格分隔，直接回车全部保留，输入 0 清空后手动填写）: ").strip()

    if raw == "":
        return tags

    if raw == "0":
        custom = input("  请输入正确的风格标签（逗号分隔）: ").strip()
        return [t.strip() for t in custom.split(",") if t.strip()]

    kept = []
    for s in raw.split():
        if s.isdigit() and 1 <= int(s) <= len(tags):
            kept.append(tags[int(s) - 1])

    extra = input("  补充标签（直接回车跳过，或输入逗号分隔）: ").strip()
    if extra:
        kept += [t.strip() for t in extra.split(",") if t.strip()]

    return kept if kept else tags


def _edit_type(current_type: str) -> str:
    """让用户纠正品类，适用于冷门/非标准单品。"""
    corrected = input(f"  品类「{current_type}」是否正确？（直接回车确认，或输入正确品类）: ").strip()
    return corrected if corrected else current_type


def beautify_image(src_path: str, description: str = "") -> str:
    """
    Beautify：悬挂/手持/背景杂乱的衣物照片 → 干净平铺商品图。
    description 非空时，指定提取图中的哪一件单品（多件同框场景必传）。
    返回生成图的本地路径。
    """
    if description:
        prompt = (
            f"从图片中提取「{description}」，"
            "生成专业电商白底平铺商品图：纯白背景，光线均匀，"
            "单件完整展开，保留颜色、印花、细节，不改变设计。"
        )
    else:
        prompt = (
            "将图中的衣物转换为专业电商平铺商品图："
            "白色或浅灰色纯色背景，光线均匀，衣物完整平铺展开，"
            "保留所有原始颜色、印花、细节，不改变设计。"
        )

    return image2_client.generate(
        prompt=prompt,
        image_paths=[src_path],
        out_dir=IMAGES_DIR,
        prefix="beautify",
    )


def save_image(src_path: str) -> str:
    os.makedirs(IMAGES_DIR, exist_ok=True)

    ext = src_path.rsplit(".", 1)[-1] if "." in src_path else "jpg"
    filename = f"{uuid.uuid4().hex}.{ext}"
    dst = os.path.join(IMAGES_DIR, filename)

    shutil.copy2(src_path, dst)
    return dst


def upload_clothing(image_path: str):
    """
    完整上传流程：识别 → 逐件用户确认（含品类纠正 + style tags 勾选）→ 存库
    返回成功入库的 item 列表。
    """
    if not os.path.isfile(image_path):
        print(f"  文件不存在或路径不是文件: {image_path}")
        return []

    print("\n📸 拍摄小贴士（识别效果更好）：")
    print("  ✓ 衣物平铺在白色 / 浅色背景上")
    print("  ✓ 光线均匀，避免强烈阴影")
    print("  ✓ 完整拍摄，不遮挡领口 / 下摆")
    print("  ✓ 一张照片可以包含多件衣物，每件会单独入库")
    print()

    print("正在识别衣物...")
    results = recognize_clothing(image_path)
    if results is None:
        return []

    total = len(results)
    print(f"\n识别到 {total} 件衣物，逐件确认：")

    saved_items = []

    beautify = input("\n图片是悬挂/手持/背景杂乱？要生成干净平铺图吗？(y/直接回车跳过): ").strip().lower()
    if beautify == "y":
        print("  正在生成 Beautify 图...")
        saved_path = beautify_image(image_path)
        print(f"  Beautify 完成: {saved_path}")
    else:
        saved_path = save_image(image_path)

    for idx, result in enumerate(results, 1):
        print(f"\n【{idx}/{total}】")
        print(f"  颜色: {', '.join(result['color'])}")
        print(f"  季节: {', '.join(result['season'])}")
        print(f"  描述: {result['description']}")

        final_type = _edit_type(result["type"])
        final_style = _edit_style_tags(result["style"])

        print(f"\n  最终入库：[{final_type}] {result['description']}  风格: {', '.join(final_style)}")
        confirm = input("  确认存入衣橱？(y/n): ").strip().lower()

        if confirm != "y":
            print("  已跳过。")
            continue

        item = {
            "item_id": str(uuid.uuid4()),
            "type": final_type,
            "color": result["color"],
            "style": final_style,
            "season": result["season"],
            "fit": result.get("fit", ""),
            "description": result["description"],
            "image_url": saved_path,
            "source": "real",
            "upload_time": datetime.now().isoformat(),
        }

        insert_wardrobe_item(item)
        print(f"  已存入衣橱！ID: {item['item_id']}")
        saved_items.append(item)

    print(f"\n本次上传完成，共存入 {len(saved_items)}/{total} 件。")
    return saved_items


def list_wardrobe():
    items = get_all_wardrobe_items()

    if not items:
        print("\n衣橱是空的，快去上传衣服吧！")
        return

    print(f"\n当前衣橱（共 {len(items)} 件）：")
    print("-" * 60)

    for i, item in enumerate(items, 1):
        print(f"{i}. [{item['type']}] {item['description']}")
        fit_str = f"  版型: {item['fit']}" if item.get("fit") else ""
        print(f"   颜色: {', '.join(item['color'])}  风格: {', '.join(item['style'])}{fit_str}")
        print(f"   ID: {item['item_id']}")
        print()