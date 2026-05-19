import os
import uuid
import shutil
from datetime import datetime

from db import upsert_user_profile, get_user_profile, get_wardrobe_item
import image2_client
import tryon_skill

IMAGES_DIR = "images"

BODY_TYPES = ["纤细", "标准", "运动", "丰满"]
STYLE_TAGS = ["休闲", "正式", "运动", "街头", "复古", "简约", "甜美"]

FIT_OPTIONS = [
    ("修身",   "穿着合身贴体，展现身材线条"),
    ("常规",   "标准版型，不宽松不贴身"),
    ("宽松",   "宽松舒适，有一定余量感"),
    ("oversize", "明显宽大落肩，大一至两码的视觉效果"),
    ("不适用", "配件/鞋履等不涉及版型的单品"),
    ("无法判断", "图片信息不足，版型无法确认"),
]

STYLING_OPTIONS = [
    ("默认", ""),
    ("敞开穿", "外套/衬衫完全敞开，里面内搭自然外露"),
    ("下摆扎入", "上衣下摆完全扎进裤子，腰线收紧"),
    ("半扎", "上衣前摆扎入裤子，后摆自然垂落"),
]




def _build_recolor_prompt(item, new_color):
    from prompt_builder import _NEGATIVE
    return (
        f"把这件{item['type']}的颜色改成{new_color}，"
        "保留所有版型、细节、印花和面料质感不变。"
        f"只改变底色，不改变任何图案、logo 或文字的颜色。负向词：{_NEGATIVE}"
    )


# ── 真实调用（key 到手后取消注释） ────────────────────────────────────────────
# import base64, requests
# from openai import OpenAI
# client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
#
# def _call_gpt_image(user_photo_path, prompt):
#     response = client.images.edit(
#         model="gpt-image-2",
#         image=open(user_photo_path, "rb"),
#         prompt=prompt,
#         n=1,
#         size="1024x1024",
#     )
#     img_data = requests.get(response.data[0].url).content
#     out_path = os.path.join(IMAGES_DIR, f"tryon_{uuid.uuid4().hex}.png")
#     with open(out_path, "wb") as f:
#         f.write(img_data)
#     return out_path
#
# def _call_recolor(image_path, prompt):
#     response = client.images.edit(
#         model="gpt-image-2",
#         image=open(image_path, "rb"),
#         prompt=prompt,
#         n=1,
#         size="1024x1024",
#     )
#     img_data = requests.get(response.data[0].url).content
#     out_path = os.path.join(IMAGES_DIR, f"recolor_{uuid.uuid4().hex}.png")
#     with open(out_path, "wb") as f:
#         f.write(img_data)
#     return out_path
# ──────────────────────────────────────────────────────────────────────────────


def _mock_gpt_image(user_photo_path, prompt):
    print(f"  [MOCK] 生成试穿图，Prompt 预览：")
    for line in prompt.split("\n"):
        print(f"         {line}")
    out_path = os.path.join(IMAGES_DIR, f"tryon_mock_{uuid.uuid4().hex}.jpg")
    shutil.copy2(user_photo_path, out_path)
    return out_path


def _mock_recolor(image_path, prompt):
    print(f"  [MOCK] 换色，Prompt 预览：{prompt}")
    out_path = os.path.join(IMAGES_DIR, f"recolor_mock_{uuid.uuid4().hex}.jpg")
    shutil.copy2(image_path, out_path)
    return out_path



def _call_image2_recolor(image_path: str, prompt: str) -> str:
    """使用 image2 服务生成换色图（单品图 + prompt）。"""
    return image2_client.generate(
        prompt=prompt,
        image_paths=[image_path],
        out_dir=IMAGES_DIR,
        prefix="recolor",
    )


COLOR_SEASONS = [
    ("冷冬", "高对比度，冷底调，适合纯白/正黑/宝蓝/玫红"),
    ("暖秋", "低对比度，暖底调，适合驼色/橄榄绿/砖红/芥末黄"),
    ("冷夏", "低对比度，冷底调，适合粉紫/灰蓝/薰衣草/裸粉"),
    ("暖春", "高对比度，暖底调，适合珊瑚红/杏色/明黄/草绿"),
]

# ── 真实调用（key 到手后取消注释）────────────────────────────────────────────
# def _call_gpt_color_season(photo_path: str) -> dict:
#     import base64
#     with open(photo_path, "rb") as f:
#         b64 = base64.b64encode(f.read()).decode()
#     ext = photo_path.rsplit(".", 1)[-1].lower()
#     mime = f"image/{ext if ext != 'jpg' else 'jpeg'}"
#     response = client.chat.completions.create(
#         model="gpt-5.4-mini",
#         messages=[{
#             "role": "user",
#             "content": [
#                 {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
#                 {"type": "text", "text": """请分析照片中人物的个人色彩季型。
# 观察维度：肤色底调（冷/暖）、肤色深浅、发色与肤色的对比度、眼睛颜色。
#
# 严格只返回 JSON，不要其他文字：
# {
#   "season": "冷冬/暖秋/冷夏/暖春",
#   "undertone": "冷/暖",
#   "contrast": "高/中/低",
#   "reason": "一句话说明判断依据",
#   "best_colors": ["颜色1", "颜色2", "颜色3", "颜色4", "颜色5"],
#   "avoid_colors": ["颜色1", "颜色2"]
# }"""}
#             ]
#         }]
#     )
#     import json as _json
#     return _json.loads(response.choices[0].message.content)
# ──────────────────────────────────────────────────────────────────────────────


def _mock_analyze_color_season(photo_path: str) -> dict:
    print(f"  [MOCK] 正在分析肤色和色季: {photo_path}")
    return {
        "season": "冷冬",
        "undertone": "冷",
        "contrast": "高",
        "reason": "肤色偏冷白，发色深，整体对比度高，属于典型冷冬型",
        "best_colors": ["纯白", "正黑", "宝蓝", "玫红", "紫色", "冰蓝"],
        "avoid_colors": ["暖米色", "橄榄绿", "橙色", "芥末黄"],
    }


def analyze_personal_color(photo_path: str) -> dict:
    """从照片分析色季，返回结构化结果"""
    if not os.path.isfile(photo_path):
        print(f"  照片不存在: {photo_path}")
        return {}
    return _mock_analyze_color_season(photo_path)  # 换真实调用时改这里


def _ask_personal_color(saved_photo: str) -> str:
    """
    色季采集：支持照片自动分析 或 手动选择。
    返回色季字符串，如 '冷冬'，跳过则返回空字符串。
    """
    print("\n--- 个人色季 ---")
    print("  1. 帮我测一下（用刚上传的照片自动分析）")
    print("  2. 我已知道自己的色季，直接选")
    print("  3. 跳过（之后可在档案里补充）")
    mode = input("请选择: ").strip()

    if mode == "1":
        result = analyze_personal_color(saved_photo)
        if not result:
            return ""
        print(f"\n  测试结果：【{result['season']}】")
        print(f"  底调：{result['undertone']}，对比度：{result['contrast']}")
        print(f"  分析：{result['reason']}")
        print(f"  适合颜色：{' / '.join(result['best_colors'])}")
        print(f"  建议避免：{' / '.join(result['avoid_colors'])}")
        confirm = input("\n  确认保存此结果？(y/n): ").strip().lower()
        return result["season"] if confirm == "y" else ""

    elif mode == "2":
        print("  请选择你的色季：")
        for i, (season, desc) in enumerate(COLOR_SEASONS, 1):
            print(f"    {i}. {season} — {desc}")
        choice = input("  请输入编号: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(COLOR_SEASONS):
            return COLOR_SEASONS[int(choice) - 1][0]
        return ""

    return ""


def _detect_skin_tone(photo_path):
    return "自然"


def _ask_fit():
    print("\n版型偏好：")
    for i, (label, desc) in enumerate(FIT_OPTIONS, 1):
        print(f"  {i}. {label} — {desc}")
    choice = input("请选择 (1-4，直接回车默认正常): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(FIT_OPTIONS):
        return FIT_OPTIONS[int(choice) - 1]
    return FIT_OPTIONS[1]  # 默认：正常


def _ask_styling():
    print("\n穿法：")
    for i, (label, desc) in enumerate(STYLING_OPTIONS, 1):
        print(f"  {i}. {label}" + (f" — {desc}" if desc else ""))
    choice = input("请选择 (1-4，直接回车默认): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(STYLING_OPTIONS):
        return STYLING_OPTIONS[int(choice) - 1]
    return STYLING_OPTIONS[0]  # 默认


def setup_user_profile(photo_path):
    if not os.path.exists(photo_path):
        raise FileNotFoundError(f"照片不存在: {photo_path}")

    print("\n--- 用户形象采集 ---")

    height = input("身高（如 170cm）: ").strip()

    print("体型选择：")
    for i, bt in enumerate(BODY_TYPES, 1):
        print(f"  {i}. {bt}")
    bt_choice = input("请选择 (1-4): ").strip()
    body_type = BODY_TYPES[int(bt_choice) - 1] if bt_choice.isdigit() and 1 <= int(bt_choice) <= 4 else "标准"

    print("风格偏好（多选，输入编号用空格分隔）：")
    for i, tag in enumerate(STYLE_TAGS, 1):
        print(f"  {i}. {tag}")
    style_input = input("请选择: ").strip().split()
    style_preference = [STYLE_TAGS[int(s) - 1] for s in style_input if s.isdigit() and 1 <= int(s) <= len(STYLE_TAGS)]

    os.makedirs(IMAGES_DIR, exist_ok=True)
    ext = photo_path.rsplit(".", 1)[-1] if "." in photo_path else "jpg"
    saved_photo = os.path.join(IMAGES_DIR, f"user_{uuid.uuid4().hex}.{ext}")
    shutil.copy2(photo_path, saved_photo)

    skin_tone = _detect_skin_tone(saved_photo)

    personal_color = _ask_personal_color(saved_photo)

    profile = {
        "user_id": "default",
        "photo_url": saved_photo,
        "height": height,
        "body_type": body_type,
        "skin_tone": skin_tone,
        "style_preference": style_preference,
        "personal_color": personal_color,
        "upload_time": datetime.now().isoformat(),
    }
    upsert_user_profile(profile)

    print("\n  用户形象已保存！")
    if personal_color:
        print(f"  色季：{personal_color}")
    return profile


def virtual_tryon(item_id):
    profile = get_user_profile("default")
    if not profile or not profile.get("photo_url"):
        print("\n  请先完成用户形象采集（选项 3）。")
        return None

    item = get_wardrobe_item(item_id)
    if not item:
        print(f"\n  找不到衣物 ID: {item_id}")
        return None

    fit_label, fit_desc = _ask_fit()
    styling_label, styling_desc = _ask_styling()

    color_override = input("\n想换个颜色试试？（直接回车跳过，或输入颜色如：砖红色）: ").strip() or ""

    fit_hint     = f"{fit_label}——{fit_desc}" if fit_desc else fit_label
    styling_hint = f"{styling_label}——{styling_desc}" if styling_desc else ""

    print(f"\n正在为你生成试穿效果：{item['description']}")
    result_path = tryon_skill.run(
        person_photo=profile["photo_url"],
        item_images=[item.get("image_url", "")],
        items=[item],
        fit_hint=fit_hint,
        styling_hint=styling_hint,
        color_override=color_override,
    )

    print(f"\n  试穿图已生成: {result_path}")
    return result_path


def recolor_item(item_id, new_color):
    item = get_wardrobe_item(item_id)
    if not item:
        print(f"\n  找不到衣物 ID: {item_id}")
        return None

    if not item.get("image_url") or not os.path.isfile(item["image_url"]):
        print("\n  该衣物没有本地图片，无法换色。")
        return None

    prompt = _build_recolor_prompt(item, new_color)
    print(f"\n正在将 [{item['description']}] 换色为：{new_color}")
    result_path = _call_image2_recolor(item["image_url"], prompt)

    print(f"\n  换色图已生成: {result_path}")
    return result_path
