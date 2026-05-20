"""
outfit_recommender.py
根据天气和衣橱数据推荐 N 套穿搭。

返回格式：
[
  {
    "item_ids":   ["id_top", "id_bottom"],   # 一套的单品 ID
    "style_tags": ["法式", "优雅"],           # 合并后的风格标签
    "caption":    "这套轻松又保暖，适合今天通勤"
  },
  ...
]
"""

import os
import random
from datetime import date as _date, timedelta
from itertools import product
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
load_dotenv()

import db

# ── 每日推荐缓存 ───────────────────────────────────────────────────────────────
# key = (user_id, date_iso, temp_bucket)，value = list[outfit]
# 同一天同温度段返回同一组结果，刷新页面不再换组（PRD v1.1 §5「默认停在第一套」）
# 衣橱变动后若缓存里 item_id 失效则自动重算。
import json

_CACHE: dict = {}          # 推荐元数据缓存：cache_key -> list[outfit]
_IMAGE_CACHE: dict = {}    # 拼图结果缓存：cache_key -> list[str(local image path)]
_GENERATING: set = set()   # 正在后台生图的 cache_key，防重复触发（不持久化，重启清零）
_STALE: set = set()        # 需要重算 outfit 的 user_id（衣橱变动时标记，不清图片）
_GEN_LOCK = __import__("threading").Lock()  # 保护 _GENERATING 的 check-and-set 原子性

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "images", "recommend_cache.json")


def _key_to_str(k: tuple) -> str:
    return "|".join(str(x) for x in k)


def _str_to_key(s: str):
    parts = s.split("|")
    return tuple(parts) if len(parts) == 3 else None


def _persist() -> None:
    """把 _CACHE + _IMAGE_CACHE 写到磁盘。uvicorn --reload 不会丢。"""
    try:
        data = {
            "outfit_cache": {_key_to_str(k): v for k, v in _CACHE.items()},
            "image_cache":  {_key_to_str(k): v for k, v in _IMAGE_CACHE.items()},
        }
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[recommender] persist cache failed: {e}")


def _load() -> None:
    """模块加载时调一次。缺失或损坏的图缓存项会被丢弃。"""
    if not os.path.isfile(_CACHE_FILE):
        return
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for s, outfits in (data.get("outfit_cache") or {}).items():
            k = _str_to_key(s)
            if k:
                _CACHE[k] = outfits
        for s, paths in (data.get("image_cache") or {}).items():
            k = _str_to_key(s)
            if k and all(os.path.isfile(p) for p in paths):
                _IMAGE_CACHE[k] = paths
    except Exception as e:
        print(f"[recommender] load cache failed: {e}")


_load()

def _temp_bucket(temp_c: float) -> str:
    if temp_c <= 5:  return "cold"
    if temp_c <= 15: return "cool"
    if temp_c <= 25: return "warm"
    return "hot"


def cache_key(user_id: str, date_iso: str, temp_c: float) -> tuple:
    return (user_id, date_iso, _temp_bucket(temp_c))


def get_cached_images(key: tuple) -> list:
    """返回缓存的拼图本地路径列表（按 outfit 顺序）。无缓存返回 []"""
    return list(_IMAGE_CACHE.get(key, []))


def set_cached_images(key: tuple, paths: list) -> None:
    _IMAGE_CACHE[key] = list(paths)
    _persist()


def is_generating(key: tuple) -> bool:
    return key in _GENERATING


def mark_generating(key: tuple, on: bool) -> None:
    if on:
        _GENERATING.add(key)
    else:
        _GENERATING.discard(key)


def claim_generating(key: tuple) -> bool:
    """原子 check-and-set：如果当前没在生图则标记并返回 True，否则返回 False。
    调用方只有拿到 True 时才应启动后台任务，避免并发重复触发。"""
    with _GEN_LOCK:
        if key in _GENERATING:
            return False
        _GENERATING.add(key)
        return True


def clear_cache(user_id: str = None) -> None:
    """主照更换时调用：outfit 元数据 + 图片缓存全清。"""
    global _CACHE, _IMAGE_CACHE
    if user_id is None:
        _CACHE.clear()
        _IMAGE_CACHE.clear()
    else:
        _CACHE       = {k: v for k, v in _CACHE.items()       if k[0] != user_id}
        _IMAGE_CACHE = {k: v for k, v in _IMAGE_CACHE.items() if k[0] != user_id}
    _STALE.discard(user_id or "default")
    _persist()


def invalidate_outfits(user_id: str = "default") -> None:
    """单品增删时调用：标记 outfit 需重算，但保留图片缓存。
    重算后若推荐的 item_ids 集合未变，图片继续复用；变了才清图。"""
    _STALE.add(user_id)

# ── 用户温感档位 ───────────────────────────────────────────────────────────────
# temp_offset 存在 user_profile.temp_offset，范围 [-10, 10]
# 正值 = 用户偏冷（同样气温穿更厚）；负值 = 用户偏热（同样气温穿更薄）
# 后续通过麦克风捕捉「好冷啊」「热死了」等反馈自动累积，见 db.update_temp_offset()

_WARMTH_THRESHOLDS = {
    "厚":   5,   # 实际感知温度 ≤ 5°C
    "中等": 15,  # 实际感知温度 ≤ 15°C
    "薄":   25,  # 实际感知温度 ≤ 25°C
}

def get_warmth_thresholds(user_id: str = "default") -> dict:
    """返回该用户的温感阈值。后续积累 memory 后动态调整，调用方无需关心偏移细节。"""
    profile = db.get_user_profile(user_id)
    offset  = profile.get("temp_offset", 0) if profile else 0
    return {level: threshold + offset for level, threshold in _WARMTH_THRESHOLDS.items()}


def _allowed_warmth(temp_c: float, user_id: str = "default") -> set:
    thresholds = get_warmth_thresholds(user_id)
    if temp_c <= thresholds["厚"]:
        return {"厚", "不适用", "无法判断"}
    if temp_c <= thresholds["中等"]:
        return {"中等", "厚", "不适用", "无法判断"}
    if temp_c <= thresholds["薄"]:
        return {"薄", "中等", "不适用", "无法判断"}
    return {"薄", "不适用", "无法判断"}


# ── Caption 生成 ───────────────────────────────────────────────────────────────

def _rule_caption(outfit_items: list, weather: dict, occasion: str = None) -> str:
    styles = []
    for it in outfit_items:
        styles.extend(it.get("style", []))
    top_style = styles[0] if styles else "简约"
    temp = weather.get("temp_c", 15)
    desc = weather.get("description", "")

    if temp <= 10:
        feel = "保暖又"
    elif temp <= 20:
        feel = "轻松又"
    else:
        feel = "清爽又"

    occ = f"适合{occasion}" if occasion else "日常穿搭"
    return f"这套{feel}{top_style}，{occ}，{temp}°C {desc}刚好合适"


def _llm_caption(outfit_items: list, weather: dict, occasion: str = None) -> str:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        styles = list({s for it in outfit_items for s in it.get("style", [])})
        types  = [it.get("type", "") for it in outfit_items]

        items_desc  = "、".join(types)
        styles_desc = "、".join(styles) if styles else "百搭"
        weather_desc = f"{weather.get('temp_c')}°C {weather.get('description', '')}".strip()

        if occasion:
            sys = (
                "你是穿搭助手。用户告诉你 ta 今天的场景，你已经选好了一套搭配，"
                "现在用一句话告诉 ta 这套为什么适合。要求：\n"
                "- 25 字以内\n"
                "- 朋友式自然口语，不要堆形容词、不要客气、不要重复用户原话\n"
                "- 直接说这套的优势 + 怎么搭场景，不要解释天气\n"
                '- 例："轻松又精神，去玩拍照好出片"、"利落不松垮，见导师不显凶"'
            )
            user = f"场景：{occasion}\n天气：{weather_desc}\n搭配：{items_desc}（风格 {styles_desc}）"
        else:
            sys = (
                "你是穿搭助手。用一句话（20 字以内）描述这套搭配的亮点，"
                "语气简洁时髦、口语化，不要堆形容词、不要废话。"
            )
            user = f"天气：{weather_desc}\n搭配：{items_desc}（风格 {styles_desc}）"

        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": sys},
                {"role": "user",   "content": user},
            ],
            max_tokens=80,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip().strip("「」\"'.")
    except Exception:
        return _rule_caption(outfit_items, weather, occasion)


# ── 场合 → 风格映射 ────────────────────────────────────────────────────────────

_OCCASION_STYLE: dict[str, list[str]] = {
    "面试":   ["通勤", "正式", "极简", "商务"],
    "工作":   ["通勤", "正式", "商务", "极简"],
    "通勤":   ["通勤", "极简", "商务"],
    "开会":   ["通勤", "正式", "商务"],
    "约会":   ["法式", "浪漫", "优雅", "甜美"],
    "逛街":   ["休闲", "街头", "极简", "法式"],
    "聚餐":   ["优雅", "法式", "休闲", "浪漫"],
    "派对":   ["街头", "甜美", "浪漫", "嘻哈"],
    "运动":   ["运动", "街头", "机能"],
    "健身":   ["运动", "机能"],
    "户外":   ["运动", "机能", "街头"],
    "旅游":   ["休闲", "运动", "街头"],
    "学校":   ["学院", "休闲", "极简"],
    "上课":   ["学院", "休闲", "极简"],
    "日常":   [],   # 不加权，用用户历史风格
    "随便":   [],
}

def _occasion_boost(occasion: str) -> dict:
    """把用户的场合描述映射为风格权重加成（叠加到 style_w 上）。"""
    if not occasion:
        return {}
    occ = occasion.strip()
    for keyword, styles in _OCCASION_STYLE.items():
        if keyword in occ:
            return {s: 1.5 for s in styles}   # 场合匹配的风格加权 1.5
    return {}


# ── 风格一致性 & 颜色搭配打分 ──────────────────────────────────────────────────

_STYLE_GROUPS: list[set] = [
    {"通勤", "正式", "商务", "职场", "OL"},
    {"休闲", "学院"},
    {"法式", "优雅", "浪漫", "甜美", "复古"},
    {"街头", "运动", "嘻哈", "机能"},
]
_BRIDGE_STYLES = {"极简", "简约"}


def _style_consistency_score(combo: list) -> float:
    """跨风格组越多越扣分，聚焦加分。"""
    groups_seen = set()
    for item in combo:
        for tag in item.get("style", []):
            if tag in _BRIDGE_STYLES:
                continue
            for i, grp in enumerate(_STYLE_GROUPS):
                if tag in grp:
                    groups_seen.add(i)
                    break
    n = len(groups_seen)
    if n <= 1:
        return 0.3
    if n == 2:
        return -0.2
    return -0.5


_COLOR_NEUTRAL = {"白", "黑", "灰", "米", "卡其", "驼", "象牙", "奶", "裸"}
_COLOR_EARTH   = {"棕", "咖", "茶", "土"}
_COLOR_COOL    = {"蓝", "绿", "紫", "青", "薄荷"}
_COLOR_WARM    = {"红", "橙", "黄", "粉", "玫", "砖", "酒"}
_COLOR_VIVID   = {"荧光", "亮黄", "明黄"}


def _classify_color(color_str: str) -> str:
    for kw in _COLOR_VIVID:
        if kw in color_str:
            return "vivid"
    for kw in _COLOR_NEUTRAL:
        if kw in color_str:
            return "neutral"
    for kw in _COLOR_EARTH:
        if kw in color_str:
            return "earth"
    for kw in _COLOR_COOL:
        if kw in color_str:
            return "cool"
    for kw in _COLOR_WARM:
        if kw in color_str:
            return "warm"
    return "unknown"


def _color_harmony_score(combo: list) -> float:
    """颜色搭配软评分，clamp 到 [-0.5, 0.5]。"""
    types, vivid_count = [], 0
    for item in combo:
        for c in item.get("color", []):
            t = _classify_color(c)
            types.append(t)
            if t == "vivid":
                vivid_count += 1

    non_neutral = [t for t in types if t not in ("neutral", "earth", "unknown")]
    score = 0.0
    if not non_neutral:
        score += 0.3
    elif len(non_neutral) == 1:
        score += 0.2
    else:
        if non_neutral.count("cool") > 0 and non_neutral.count("warm") > 0:
            score -= 0.2
    if vivid_count >= 2:
        score -= 0.3
    return max(-0.5, min(0.5, score))


# ── 排序：风格档案加权 + 最近穿过惩罚 ──────────────────────────────────────────

def _style_weights(user_id: str) -> dict:
    """从 user_profile.style_tags 推断用户偏好风格权重（衰减赋权）。"""
    profile = db.get_user_profile(user_id) or {}
    tags    = profile.get("style_tags") or []
    if not isinstance(tags, list):
        return {}
    return {t: max(0.2, 1.0 - i * 0.2) for i, t in enumerate(tags[:5])}


def _recent_item_sets(user_id: str, days: int = 7) -> list:
    """最近 N 天 looks 的 item_id 集合（用于重复惩罚）。"""
    try:
        looks = db.get_looks(user_id=user_id, limit=50)
    except Exception:
        return []
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    return [set(lk.get("item_ids", [])) for lk in looks if lk.get("date", "") >= cutoff]


def _score_combo(combo: list, style_w: dict, recent_sets: list) -> float:
    """综合打分：风格偏好加分 + 最近穿过惩罚 + 风格一致性 + 颜色搭配。"""
    score = 0.0

    # 用户风格偏好加分
    styles = {s for it in combo for s in it.get("style", [])}
    if style_w and styles:
        score += sum(style_w.get(s, 0) for s in styles)

    # 最近 7 天重复惩罚
    combo_ids = {it["item_id"] for it in combo}
    for recent in recent_sets:
        if not recent:
            continue
        overlap = len(combo_ids & recent) / max(len(combo_ids), 1)
        if overlap >= 0.3:
            score -= overlap

    # 风格一致性 & 颜色搭配
    score += _style_consistency_score(combo)
    score += _color_harmony_score(combo)

    return score


# ── 两阶段组合逻辑 ─────────────────────────────────────────────────────────────

_K_SLOT  = 10   # 上装 / 下装 / 全身每 slot 召回上限
_K_OUTER = 3    # 外套召回上限
_K_SHOE  = 6    # 鞋履召回上限（扩大让跨套换鞋有更多选择）

_COLD_ONLY_TYPES = {"帽子", "围巾"}   # 只在气温 ≤10°C 时纳入候选


def _acc_style_sim(acc_styles: set, outfit_styles: set) -> float:
    """Jaccard 相似度：两个风格集合的交集 / 并集。"""
    if not acc_styles or not outfit_styles:
        return 0.0
    return len(acc_styles & outfit_styles) / len(acc_styles | outfit_styles)


def _pick_accessory(combo: list, accessories: list, cold: bool, exclude: set = None):
    """outfit 定好后，从配件池里召回风格最相似的一件。
    - 围巾/帽子仅在 cold=True 时纳入候选（防止夏天推围巾）
    - exclude: 已被其他套装选走的配件 id，优先选未用过的
    """
    if not accessories:
        return None

    exclude = exclude or set()
    outfit_styles = {s for it in combo for s in it.get("style", [])}

    candidates = [
        a for a in accessories
        if not (any(t in (a.get("type") or "") for t in _COLD_ONLY_TYPES) and not cold)
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda a: (
            1 if a["item_id"] in exclude else 0,   # 未用过优先
            -_acc_style_sim(set(a.get("style", [])), outfit_styles),
        ),
    )
    return candidates[0]


def _retrieve_slot(items: list, category: str, allowed_warmth: set,
                   style_w: dict, k: int) -> tuple:
    """Stage-1 召回：硬过滤冷暖 → 风格排序 → top-k。
    返回 (top_k_list, fallback_ids)，fallback_ids 为放宽限制后补入的单品 ID。"""
    pool = [it for it in items if it.get("category") == category]

    # 鞋履/配件不受温度限制
    if category in ("鞋履", "配件"):
        filtered, fallback_ids = list(pool), set()
    else:
        filtered     = [it for it in pool if (it.get("warmth") or "不适用") in allowed_warmth]
        fallback_ids = set()
        if len(filtered) < k:
            seen = {it["item_id"] for it in filtered}
            for it in pool:
                if it["item_id"] not in seen:
                    filtered.append(it)
                    fallback_ids.add(it["item_id"])
                if len(filtered) >= k:
                    break

    filtered.sort(
        key=lambda it: sum(style_w.get(s, 0) for s in it.get("style", [])),
        reverse=True,
    )
    return filtered[:k], fallback_ids


def _form_combos(tops: list, bottoms: list, fulls: list,
                 outers: list, shoes: list, cold: bool) -> list:
    """Stage-2 组合：top-k 召回集两两配对，外套/鞋各自参与组合而非只取第一件。"""
    outer_opts = outers if (cold and outers) else [None]
    shoe_opts  = shoes  if shoes             else [None]

    combos = []
    for top, bottom in product(tops, bottoms):
        for outer in outer_opts:
            for shoe in shoe_opts:
                combo = [top, bottom]
                if outer: combo.append(outer)
                if shoe:  combo.append(shoe)
                combos.append(combo)
    for full in fulls:
        for outer in outer_opts:
            for shoe in shoe_opts:
                combo = [full]
                if outer: combo.append(outer)
                if shoe:  combo.append(shoe)
                combos.append(combo)
    return combos


_RERANK_THRESHOLD = 1.0   # rule score 低于此值时触发 GPT-4o rerank


def _fmt_combo(combo: list) -> str:
    parts = []
    for it in combo:
        color = "、".join(it.get("color") or [])
        style = "、".join(it.get("style") or [])
        parts.append(f"{color}{it.get('type', '')}({style})")
    return " + ".join(parts)


def _gpt_rerank(candidates: list, weather: dict, occasion: str = None) -> list:
    """GPT-4o 对候选组合重排序，返回重排后列表；失败时返回原顺序。"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=30.0)

        outfit_lines = "\n".join(
            f"套装{i+1}: {_fmt_combo(c)}" for i, c in enumerate(candidates)
        )
        temp_c = weather.get("temp_c", 15)
        desc   = weather.get("description", "")
        occ_line = f"场合：{occasion}\n" if occasion else ""

        system = (
            "你是专业穿搭顾问。根据以下规则对候选套装从好到差排序，"
            "只返回套装编号，用英文逗号分隔，例如：3,1,4,2\n\n"
            "排序规则（优先级从高到低）：\n"
            "1. 风格一致性：同一风格系（正装/休闲/优雅/街头）内搭配优先；极简/简约可与任何风格搭\n"
            "2. 颜色搭配：中性色（白/黑/灰/米/卡其）组合最稳；1件点缀色+其余中性色好；冷暖混搭扣分\n"
            "3. 整体和谐：版型、材质、场合是否协调\n"
            "4. 实际可穿：真实日常中真正会穿的组合"
        )
        user = (
            f"天气：{temp_c}°C {desc}\n"
            f"{occ_line}"
            f"候选套装：\n{outfit_lines}\n\n"
            "请从最好到最差排序，只返回编号。"
        )

        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=60,
            temperature=0,
        )

        raw = resp.choices[0].message.content.strip()
        indices = []
        seen_idx = set()
        for tok in raw.replace("，", ",").split(","):
            tok = tok.strip()
            if tok.isdigit():
                idx = int(tok) - 1
                if 0 <= idx < len(candidates) and idx not in seen_idx:
                    seen_idx.add(idx)
                    indices.append(candidates[idx])
        # 补上 GPT-4o 没提到的候选（不丢弃）
        for i, c in enumerate(candidates):
            if i not in seen_idx:
                indices.append(c)
        print(f"  [recommender] GPT-4o rerank 完成，{len(candidates)} 套候选重排")
        return indices

    except Exception as e:
        print(f"  [recommender] GPT-4o rerank 失败: {e}")
        return candidates


def _make_outfits(items: list, weather: dict, occasion: str, n: int, user_id: str = "default") -> list:
    allowed     = _allowed_warmth(weather.get("temp_c", 15), user_id)
    cold        = weather.get("temp_c", 15) <= 10
    style_w     = _style_weights(user_id)
    # 场合有效时把场合对应风格权重叠加（取最大值，不覆盖用户历史偏好）
    for style, w in _occasion_boost(occasion).items():
        style_w[style] = max(style_w.get(style, 0), w)
    recent_sets = _recent_item_sets(user_id)

    # Stage 1：每 slot 独立召回 top-K（硬过滤冷暖 → 风格排序）
    tops,    tops_fb    = _retrieve_slot(items, "上装", allowed, style_w, _K_SLOT)
    bottoms, bottoms_fb = _retrieve_slot(items, "下装", allowed, style_w, _K_SLOT)
    fulls,   fulls_fb   = _retrieve_slot(items, "全身", allowed, style_w, _K_SLOT // 2)
    outers,  outers_fb  = _retrieve_slot(items, "外套", allowed, style_w, _K_OUTER)
    shoes,   shoes_fb   = _retrieve_slot(items, "鞋履", allowed, style_w, _K_SHOE)
    fallback_ids = tops_fb | bottoms_fb | fulls_fb | outers_fb | shoes_fb

    # Stage 2：组合 + 打分排序
    candidates = _form_combos(tops, bottoms, fulls, outers, shoes, cold)
    if not candidates:
        return []

    scored = [
        (_score_combo(c, style_w, recent_sets) + random.random() * 0.1, c)
        for c in candidates
    ]
    scored.sort(key=lambda x: -x[0])

    # 规则分低于阈值时，GPT-4o 介入对 top-20 候选重排
    top_score = scored[0][0] if scored else 0
    if top_score < _RERANK_THRESHOLD:
        pool = _gpt_rerank([c for _, c in scored[:20]], weather, occasion)
    else:
        pool = [c for _, c in scored]

    # 第一轮：上装/下装/全身 严格不重复（每件只能出现在一套里）
    # 鞋履/配件不做限制（允许跨套复用）
    # 不用风格组约束，避免衣橱风格单一时过早触发 fallback
    _DOMINANT_CATS = {"上装", "下装", "全身"}
    seen_keys = set()
    top_combos = []
    used_dominant: set = set()   # 已被选中的上装/下装/全身 item_id

    for combo in pool:
        if len(top_combos) >= n:
            break
        key = tuple(sorted(it["item_id"] for it in combo))
        if key in seen_keys:
            continue
        seen_keys.add(key)

        combo_ids    = {it["item_id"] for it in combo}
        dominant_ids = {it["item_id"] for it in combo if it.get("category") in _DOMINANT_CATS}

        if dominant_ids & used_dominant:   # 有上装或下装已被其他套装占用
            continue

        has_warning = bool(combo_ids & fallback_ids)
        top_combos.append((combo, has_warning))
        used_dominant |= dominant_ids

    # 第二轮 fallback：上装不够 n 件时（衣橱太小），放宽约束按分数补足
    if len(top_combos) < n:
        selected_keys = {tuple(sorted(it["item_id"] for it in combo)) for combo, _ in top_combos}
        for combo in pool:
            if len(top_combos) >= n:
                break
            key = tuple(sorted(it["item_id"] for it in combo))
            if key in selected_keys:
                continue
            combo_ids = {it["item_id"] for it in combo}
            has_warning = bool(combo_ids & fallback_ids)
            top_combos.append((combo, has_warning))
            selected_keys.add(key)

    if not top_combos:
        return []

    # Stage 2.5：给每套追加一件配件，已用过的配件降权，保证不同套装尽量用不同配件
    accessories = [it for it in items if it.get("category") == "配件"]
    if accessories:
        used_acc_ids: set = set()
        new_top_combos = []
        for combo, hw in top_combos:
            acc = _pick_accessory(combo, accessories, cold, exclude=used_acc_ids)
            if acc:
                used_acc_ids.add(acc["item_id"])
                combo = combo + [acc]
            new_top_combos.append((combo, hw))
        top_combos = new_top_combos

    # Stage 3：并发 LLM caption（全套，不再只生成第一套）
    def _caption(combo):
        return _llm_caption(combo, weather, occasion)

    with ThreadPoolExecutor(max_workers=min(len(top_combos), 4)) as executor:
        captions = list(executor.map(_caption, [c for c, _ in top_combos]))

    return [
        {
            "item_ids":       [it["item_id"] for it in combo],
            "style_tags":     list(dict.fromkeys(s for it in combo for s in it.get("style", []))),
            "caption":        caption,
            "warmth_warning": has_warning,
        }
        for (combo, has_warning), caption in zip(top_combos, captions)
    ]


# ── 公开接口 ───────────────────────────────────────────────────────────────────

def precompute_for_date(
    user_id: str,
    weather: dict,
    date_iso: str,
    n: int = 4,
) -> list:
    """预生成指定日期的推荐，写入 _CACHE。供 Tomorrow Planning 用。

    与 `recommend_outfits` 的区别：cache_key 用传入的 date_iso（明日）而不是 today。
    返回的 outfits 同样会被存入 _CACHE，次日 /api/recommend 命中后直接拿。
    """
    items = db.get_all_wardrobe_items(source_filter="real")
    if not items:
        return []
    outfits = _make_outfits(items, weather, None, max(n, 6), user_id)
    if outfits:
        key = (user_id, date_iso, _temp_bucket(weather.get("temp_c", 15)))
        _CACHE[key] = outfits
        _persist()
    return outfits[:n]


def _cache_still_valid(cached: list, current_ids: set) -> bool:
    """缓存里的 item_id 必须全在当前衣橱中，否则视为失效。"""
    for o in cached:
        if not all(iid in current_ids for iid in o.get("item_ids", [])):
            return False
    return True


def recommend_outfits(
    user_id: str,
    weather: dict,
    occasion: str = None,
    n: int = 4,
    _mock_items: list = None,   # 仅供测试注入，生产不传
    refresh: bool = False,      # 强制重算，跳过缓存
) -> list:
    # 测试注入模式：永远不走缓存，方便隔离
    if _mock_items is not None:
        return _make_outfits(_mock_items, weather, occasion, n, user_id)

    items     = db.get_all_wardrobe_items(source_filter="real")
    item_ids  = {it["item_id"] for it in items}
    cache_key = (user_id, _date.today().isoformat(), _temp_bucket(weather.get("temp_c", 15)))

    # 用户明确说了场合 → 不读不写缓存，本次按场合实时算
    bypass_cache = bool(occasion)

    stale = user_id in _STALE
    if not refresh and not bypass_cache and not stale and cache_key in _CACHE:
        cached = _CACHE[cache_key]
        if cached and len(cached) >= n and _cache_still_valid(cached, item_ids):
            return cached[:n]

    # 多生成几套备着（缓存够用 + 给「换一组」留余量）
    outfits = _make_outfits(items, weather, occasion, max(n, 6), user_id)
    if outfits and not bypass_cache:
        # 只比 item_ids 集合，忽略 caption / style_tags 差异（LLM 每次结果不同）
        old_id_sets = {frozenset(o["item_ids"]) for o in _CACHE.get(cache_key, [])}
        new_id_sets = {frozenset(o["item_ids"]) for o in outfits}
        if old_id_sets != new_id_sets:
            _IMAGE_CACHE.pop(cache_key, None)
        _CACHE[cache_key] = outfits
        _STALE.discard(user_id)
        _persist()
    return outfits[:n]
