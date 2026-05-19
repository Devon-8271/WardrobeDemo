import base64
import json
import os
from pathlib import Path
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4.1-mini")
TEXT_MODEL = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4.1-mini")


def image_to_data_url(image_path: str) -> str:
    path = Path(image_path)
    suffix = path.suffix.lower()

    if suffix in [".jpg", ".jpeg"]:
        mime = "image/jpeg"
    elif suffix == ".png":
        mime = "image/png"
    elif suffix == ".webp":
        mime = "image/webp"
    else:
        mime = "image/jpeg"

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime};base64,{b64}"


def analyze_clothing_image(image_path: str) -> dict:
    image_url = image_to_data_url(image_path)

    prompt = """
你是一个服装识别助手。请识别图片中的单件服装，并严格输出 JSON。
不要输出 markdown，不要解释。

字段要求：
{
  "category": "上装|下装|全身|外套|鞋履|配件",
  "type": "具体类型，例如 T恤/衬衫/半身裙/连衣裙/牛仔裤/西装外套/运动鞋",
  "raw_type": "你原始判断的服装名称",
  "color": ["1-3个主色"],
  "style": ["1-4个风格标签"],
  "season": ["春|夏|秋|冬"],
  "warmth": "薄|中等|厚|不适用|无法判断",
  "fit": "修身|常规|宽松|oversize|不适用|无法判断",
  "description": "15-30字中文描述"
}

只输出合法 JSON。
"""

    response = client.responses.create(
        model=VISION_MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url},
                ],
            }
        ],
    )

    text = response.output_text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError(f"OpenAI vision returned non-JSON output: {text}")