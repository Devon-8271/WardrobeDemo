"""
pose_engine.py
衣橱标签 → 视觉姿势语言翻译层。

不直接把结构化标签喂给 image2；
而是把 style / fit / category 翻译成 image2 能理解的自然语言姿势指令。

调用方式：
    from pose_engine import build_pose_hint
    hint = build_pose_hint(item)               # 单品试穿
    hint = build_pose_hint(item, ootd_items)   # OOTD 整体试穿
"""

import random

_POWER = {
    "街头", "工装", "机能", "Gorpcore", "中性", "帅", "酷",
    "美式", "硬核", "朋克", "暗黑", "嘻哈", "户外",
}
_SOFT = {
    "温柔", "甜美", "法式", "韩系", "学院", "仙女", "少女",
    "优雅", "清新", "浪漫", "日系", "复古少女", "轻熟",
}
_FORMAL = {
    "通勤", "OL", "商务", "极简", "正式", "职场",
    "简约", "高级感", "静奢",
}

_POSE_POOLS = {
    "power": [
        "双手插口袋，身体正面，肩膀打开，重心均匀",
        "双手抱臂，直视前方，气场沉稳",
        "靠墙站立，单手插袋，神态随意放松",
        "蹲姿，双肘撑膝，自信放松",
        "行走抓拍，步幅较大，肩膀舒展",
        "回头，侧身，单手扶帽沿或插袋",
    ],
    "soft": [
        "微侧身 45°，单腿重心，手轻扶发梢",
        "手提包带，低头微笑，身体侧向",
        "行走抓拍，步伐轻盈，衣摆自然飘动",
        "回眸站姿，身体侧转，视线回望镜头",
        "坐姿，腿部侧放交叠，手轻放膝上",
        "单手扶肩带，另一手自然垂落，面带微笑",
    ],
    "formal": [
        "侧身站立，单手插口袋，另一手自然垂落",
        "行走抓拍，步态稳健，目视前方",
        "正面站立，双手自然垂落或持包，姿态挺拔",
        "坐姿前倾，双手交叠，神态干练",
        "手扶翻领或袖口，低头整理的自然瞬间",
    ],
}

_CATEGORY_BONUS = {
    "裙子": "呈现裙摆动态或层次感，腿部线条完整入镜",
    "外套": "外套可自然敞开，内搭清晰可见",
    "鞋履": "全身完整入镜，鞋部细节清晰",
    "下装": "腿部线条完整入镜，裤线或裙摆清晰",
    "全身": "整体廓形完整呈现，头顶到脚底全身入镜",
}


def _score_vibe(styles: set, fits: set) -> str:
    power  = len(styles & _POWER)  + (1 if {"oversize", "宽松"} & fits else 0)
    soft   = len(styles & _SOFT)   + (1 if "修身" in fits else 0)
    formal = len(styles & _FORMAL)
    scores = {"power": power, "soft": soft, "formal": formal}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "soft"


def build_pose_hint(item: dict, ootd_items: list = None, n: int = 3) -> str:
    """
    从单品标签（+ 可选 OOTD 上下文）推断姿势建议。
    返回可直接注入 image2 prompt 的自然语言段落。

    ootd_items: 搭配中其他单品的 dict 列表。
                单品试穿不传，OOTD 整套试穿时传入其余单品以修正整体风格判断。
    n: 候选姿势数量，image2 选取最契合的一种。
    """
    all_styles = set(item.get("style", []))
    all_fits   = {item.get("fit", "")}

    if ootd_items:
        for it in ootd_items:
            all_styles.update(it.get("style", []))
            all_fits.add(it.get("fit", ""))

    vibe = _score_vibe(all_styles, all_fits)
    pool = _POSE_POOLS[vibe]
    selected = random.sample(pool, min(n, len(pool)))

    category = item.get("category", "")
    bonus = _CATEGORY_BONUS.get(category, "")
    style_label = "、".join(sorted(all_styles)[:4]) if all_styles else "日常"

    lines = [
        f"整体穿搭风格：{style_label}",
        f"从以下姿势中选取最契合风格的一种生成：",
    ]
    for i, pose in enumerate(selected, 1):
        lines.append(f"  {i}. {pose}")
    if bonus:
        lines.append(f"构图备注：{bonus}")
    lines.append("动作自然真实，与整体穿搭气质保持一致。")

    return "\n".join(lines)
