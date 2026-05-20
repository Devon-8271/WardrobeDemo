"""
scene_engine.py — 背景场景选择器

设计原则（四层分离）：
    _STYLE_MOOD            → 气质（formal / soft / power）
    _SCENE_HINT            → 地点（occasion 或 style 关键词 → scene_group）
    _STRONG_LOCATION_HINTS → 允许从 style tag 直接触发场景的强地点词白名单
    _STYLE_TO_DEFAULT_SCENE → mood × weekend 默认场景（无关键词时兜底，不含 home_mirror）
    _SCENE_MAP             → scene_group → variant 列表（含 id / prompt / best_for / occasion / photo_mode / default_weight）

数据流：
    用户 occasion "约会" → _SCENE_HINT["约会"] → "date_restaurant"
    单品 style=["度假"]  → 度假不在 _STRONG_LOCATION_HINTS，不触发
    单品 style=["海边"]  → 海边在 _STRONG_LOCATION_HINTS → "travel_vacation"
    无关键词              → _STYLE_TO_DEFAULT_SCENE[mood][weekend] → scene_group
    scene_group           → pick_scene_variant() → 最优 variant → prompt + variant_id

Variant 选择：
    两级打分 + default_weight 基底：
      - default_weight 保证无信号时不纯随机
      - occasion 命中 variant.occasion（+3.0）
      - mood 命中 variant.best_for（+2.0）
    从 top-2 中随机选取，grid 四格通过 exclude_ids 去重。
    variant id 全局唯一（格式 scene_group/variant_id）。

向后兼容：
    pick_scene() 保留旧接口，返回 str（prompt）。
    pick_scene_with_variant() 返回 (prompt, variant_id)。
    所有公开函数预留 user_context 参数（None 时不影响现有调用）。
"""

import random
from datetime import date as _date

# ── 1. 风格标签 → 气质 mood ─────────────────────────────────────────────────────
_STYLE_MOOD = {
    "通勤": "formal", "OL": "formal", "职场": "formal", "商务": "formal",
    "正式": "formal", "礼服": "formal", "极简": "formal", "简约": "formal",
    "基础款": "formal", "老钱": "formal", "静奢": "formal", "高级感": "formal",
    "中性": "formal", "知性": "formal", "干练": "formal", "利落": "formal",
    "clean fit": "formal", "quiet luxury": "formal", "smart casual": "formal",

    "法式": "soft", "优雅": "soft", "气质": "soft", "复古": "soft",
    "温柔": "soft", "韩系": "soft", "日系": "soft", "学院": "soft",
    "文艺": "soft", "松弛": "soft", "慵懒": "soft", "清新": "soft",
    "度假": "soft", "波西米亚": "soft", "海岛": "soft", "田园": "soft",
    "clean girl": "soft", "复古学院": "soft",

    "街头": "power", "嘻哈": "power", "美式": "power", "运动": "power",
    "机能": "power", "工装": "power", "户外": "power", "酷感": "power",
    "酷飒": "power", "辣妹": "power", "Y2K": "power", "千禧": "power",
    "牛仔": "power", "皮衣": "power", "摇滚": "power", "高街": "power",
    "oversize": "power", "boyish": "power", "赛车": "power", "摩托": "power",
    "赛博": "power", "未来感": "power", "先锋": "power",
}

# ── 2. 关键词 → 场景组（occasion / style keyword → scene_group）──────────────────
_SCENE_HINT = {
    # 通勤 / 办公
    "通勤": "commute_street",
    "上班": "office_clean",
    "职场": "office_clean",
    "商务": "office_clean",
    "会议": "office_clean",
    "办公室": "office_clean",

    # 约会 / 餐饮
    "约会": "date_restaurant",
    "餐厅": "date_restaurant",
    "咖啡馆": "date_restaurant",
    "brunch": "date_restaurant",
    "下午茶": "date_restaurant",
    "婚礼": "date_restaurant",

    # 旅行 / 度假
    "旅行": "travel_vacation",
    "度假": "travel_vacation",
    "海边": "travel_vacation",
    "海岛": "travel_vacation",
    "沙滩": "travel_vacation",
    "酒店": "travel_vacation",
    "机场": "travel_vacation",
    "露营": "travel_vacation",

    # 逛街 / 周末
    "逛街": "weekend_market",
    "周末": "weekend_market",
    "市集": "weekend_market",
    "买手店": "weekend_market",
    "书店": "weekend_market",
    "花市": "weekend_market",

    # 派对 / 夜生活
    "派对": "party_night",
    "酒吧": "party_night",
    "夜店": "party_night",
    "音乐节": "party_night",
    "livehouse": "party_night",
    "晚宴": "party_night",
    "酒会": "party_night",

    # 校园（仅强信号触发）
    "校园": "campus_casual",
    "上课": "campus_casual",
    "图书馆": "campus_casual",
    "大学": "campus_casual",

    # 镜自拍
    "镜自拍": "home_mirror",
    "试衣间": "home_mirror",
    "衣帽间": "home_mirror",
    "全身镜": "home_mirror",
    "电梯镜": "home_mirror",

    # 日常 / 拍照
    "日常": "commute_street",
    "拍照": "commute_street",
    "运动": "commute_street",
    "健身": "commute_street",
}

# ── 2b. 强地点词白名单（只有这些词命中 style tag 时才触发场景）─────────────────
# 宽松词（度假/通勤/运动/日常等）不在此白名单，避免 style tag 误判场景。
_STRONG_LOCATION_HINTS = {
    "校园", "上课", "图书馆", "大学",
    "镜自拍", "试衣间",
    "派对", "酒吧", "夜店", "音乐节", "livehouse",
    "海边", "海岛", "沙滩", "机场", "露营",
}

# ── 3. mood × weekend → 默认场景组（不含 home_mirror）─────────────────────────
_STYLE_TO_DEFAULT_SCENE = {
    "formal": {
        False: "office_clean",
        True:  "commute_street",
    },
    "soft": {
        False: "commute_street",
        True:  "date_restaurant",
    },
    "power": {
        False: "commute_street",
        True:  "weekend_market",
    },
}

# ── 4. 场景组 → variant 列表 ────────────────────────────────────────────────────
# variant id 全局唯一（格式 scene_group/variant_name）
# default_weight: 无信号时的基底权重，避免纯随机
_SCENE_MAP = {
    "commute_street": [
        {
            "id": "commute_street/office_glass_street",
            "prompt": (
                "modern city street outside an office building, early morning, "
                "cool diffused overcast light, glass facade reflecting sky, "
                "light pedestrian traffic in background, photorealistic"
            ),
            "best_for": {"formal"},
            "occasion": {"通勤", "上班", "职场"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
        {
            "id": "commute_street/crosswalk_morning",
            "prompt": (
                "crosswalk at morning rush hour, soft golden sunrise light "
                "filtering through high-rise buildings, light traffic in background, "
                "urban professional atmosphere, photorealistic"
            ),
            "best_for": {"formal"},
            "occasion": {"通勤", "上班"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
        {
            "id": "commute_street/tree_boulevard_cafe",
            "prompt": (
                "tree-lined urban boulevard, dappled sunlight through plane tree leaves, "
                "boutique shops and café terraces in background, quiet morning mood, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"日常", "逛街", "citywalk"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
        {
            "id": "commute_street/transit_plaza",
            "prompt": (
                "subway entrance or transit hub plaza, natural daylight, "
                "commuters passing by, modern urban architecture with steel and glass, photorealistic"
            ),
            "best_for": {"formal", "power"},
            "occasion": {"通勤"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
    ],

    "office_clean": [
        {
            "id": "office_clean/corporate_lobby",
            "prompt": (
                "modern conference center or corporate lobby interior, "
                "clean marble floor, neutral cool ambient light from large windows, "
                "minimal and professional atmosphere, photorealistic"
            ),
            "best_for": {"formal"},
            "occasion": {"上班", "职场", "商务", "会议"},
            "photo_mode": "indoor_clean",
            "default_weight": 1.0,
        },
        {
            "id": "office_clean/minimal_corridor",
            "prompt": (
                "minimalist corridor with floor-to-ceiling windows, "
                "clean architectural lines, soft afternoon daylight casting long shadows, "
                "professional atmosphere, photorealistic"
            ),
            "best_for": {"formal", "soft"},
            "occasion": {"上班", "职场", "商务"},
            "photo_mode": "indoor_clean",
            "default_weight": 1.0,
        },
        {
            "id": "office_clean/creative_workspace",
            "prompt": (
                "open-plan creative workspace, warm natural light from skylight, "
                "indoor plants and modern wooden furniture in background, "
                "relaxed but polished, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"上班", "日常"},
            "photo_mode": "indoor_clean",
            "default_weight": 1.0,
        },
        {
            "id": "office_clean/coworking_lounge",
            "prompt": (
                "co-working lounge area, warm wood tones, natural daylight, "
                "bookshelves and greenery in background, "
                "professional yet inviting, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"日常"},
            "photo_mode": "indoor_clean",
            "default_weight": 1.0,
        },
    ],

    "date_restaurant": [
        {
            "id": "date_restaurant/rooftop_terrace",
            "prompt": (
                "rooftop restaurant terrace at dusk, "
                "warm amber and rose sky, city skyline in background, "
                "soft candlelight and string lights, "
                "refined atmospheric glow, photorealistic"
            ),
            "best_for": {"soft", "formal"},
            "occasion": {"约会", "餐厅", "晚宴"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "date_restaurant/courtyard_cafe",
            "prompt": (
                "intimate courtyard garden café, fairy string lights overhead, "
                "lush greenery and brick walls, golden hour sunlight filtering through leaves, "
                "romantic and relaxed, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"约会", "咖啡馆", "下午茶", "brunch"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "date_restaurant/indoor_restaurant_window",
            "prompt": (
                "chic indoor restaurant by a large window, "
                "warm pendant lighting, blurred city view through glass, "
                "elegant but relaxed table setting, photorealistic"
            ),
            "best_for": {"formal", "soft"},
            "occasion": {"约会", "餐厅"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "date_restaurant/waterfront_bistro",
            "prompt": (
                "waterfront boardwalk bistro at sunset, "
                "warm golden light reflecting on water, distant city skyline, "
                "gentle breeze, romantic atmosphere, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"约会", "度假", "旅行"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "date_restaurant/garden_wedding_venue",
            "prompt": (
                "elegant garden wedding venue, lush floral arches and white roses, "
                "soft golden hour sunlight through weeping willows, "
                "manicured lawn and champagne tables in background, "
                "romantic and refined celebration atmosphere, photorealistic"
            ),
            "best_for": {"soft", "formal"},
            "occasion": {"婚礼"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
    ],

    "travel_vacation": [
        {
            "id": "travel_vacation/beachside_promenade",
            "prompt": (
                "beachside promenade at golden hour, "
                "warm low sunlight, ocean and palm trees in background, "
                "clear air, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"海边", "海岛", "沙滩", "度假"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "travel_vacation/med_old_town",
            "prompt": (
                "Mediterranean old town narrow street, "
                "warm stone walls and bougainvillea flowers, "
                "late afternoon sunlight, distant sea view, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"旅行", "度假"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "travel_vacation/resort_garden",
            "prompt": (
                "tropical resort garden pathway, lush green foliage, "
                "soft dappled sunlight, glimpse of turquoise ocean in distance, "
                "relaxed vacation atmosphere, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"度假", "酒店"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "travel_vacation/city_museum_street",
            "prompt": (
                "historic city street near a museum or gallery, "
                "warm afternoon light on classical architecture, "
                "tree-lined sidewalk with café tables, cultural travel atmosphere, photorealistic"
            ),
            "best_for": {"formal", "soft"},
            "occasion": {"旅行", "城市"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
    ],

    "weekend_market": [
        {
            "id": "weekend_market/flea_market_plaza",
            "prompt": (
                "weekend outdoor flea market or park plaza, "
                "natural warm daylight, green trees in background, "
                "relaxed crowd in background, photorealistic"
            ),
            "best_for": {"soft", "power"},
            "occasion": {"逛街", "周末", "市集"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "weekend_market/flower_market_street",
            "prompt": (
                "flower market street lined with colorful fresh blooms and potted plants, "
                "morning sunlight, charming storefronts, "
                "lively but relaxed atmosphere, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"逛街", "花市", "周末"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "weekend_market/bookstore_gallery",
            "prompt": (
                "charming independent bookstore or art gallery street front, "
                "warm afternoon light, wooden storefront and window displays, "
                "quiet and cultured atmosphere, photorealistic"
            ),
            "best_for": {"formal", "soft"},
            "occasion": {"逛街", "书店", "周末"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
        {
            "id": "weekend_market/artisan_market_tents",
            "prompt": (
                "artisan craft fair under open white tents, "
                "soft daylight, handmade goods on display, "
                "creative and relaxed atmosphere, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"逛街", "市集", "周末"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
    ],

    "party_night": [
        {
            "id": "party_night/gallery_opening",
            "prompt": (
                "art gallery opening or private cocktail event, "
                "soft track lighting, modern artwork on walls, "
                "chic crowd in background, sophisticated evening atmosphere, photorealistic"
            ),
            "best_for": {"formal", "soft"},
            "occasion": {"晚宴", "酒会"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "party_night/rooftop_bar",
            "prompt": (
                "rooftop bar at night with city skyline backdrop, "
                "warm amber glow from bar counter, "
                "sophisticated evening crowd in distance, photorealistic"
            ),
            "best_for": {"power", "soft"},
            "occasion": {"派对", "酒吧"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "party_night/hotel_lounge",
            "prompt": (
                "hotel lounge with velvet seating and marble details, "
                "dim amber lighting, moody and refined evening atmosphere, "
                "elegant night out, photorealistic"
            ),
            "best_for": {"formal", "soft"},
            "occasion": {"晚宴", "酒会", "派对"},
            "photo_mode": "indoor_clean",
            "default_weight": 1.0,
        },
        {
            "id": "party_night/livehouse_venue",
            "prompt": (
                "intimate music venue or livehouse, "
                "warm stage lighting reflecting on exposed brick walls, "
                "energetic but stylish night atmosphere, photorealistic"
            ),
            "best_for": {"power"},
            "occasion": {"音乐节", "livehouse", "派对", "夜店"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
    ],

    "campus_casual": [
        {
            "id": "campus_casual/campus_walkway",
            "prompt": (
                "university campus walkway, morning light filtering through trees, "
                "brick buildings and green lawn in background, "
                "casual student atmosphere, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"校园", "上课", "大学"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
        {
            "id": "campus_casual/library_plaza",
            "prompt": (
                "library main steps or plaza, soft afternoon sunlight, "
                "students passing by in distance, ivy-covered historic building facade, "
                "academic atmosphere, photorealistic"
            ),
            "best_for": {"formal", "soft"},
            "occasion": {"校园", "图书馆", "大学"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
        {
            "id": "campus_casual/campus_garden_quad",
            "prompt": (
                "campus garden quad, shade under a large old tree, "
                "casual student activity in background, "
                "relaxed and youthful atmosphere, photorealistic"
            ),
            "best_for": {"soft"},
            "occasion": {"校园", "大学"},
            "photo_mode": "lifestyle_candid",
            "default_weight": 1.0,
        },
        {
            "id": "campus_casual/modern_campus_courtyard",
            "prompt": (
                "modern campus courtyard, concrete and glass architecture, "
                "young trees and wooden benches, morning light, "
                "clean and contemporary academic setting, photorealistic"
            ),
            "best_for": {"formal"},
            "occasion": {"校园", "上课", "大学"},
            "photo_mode": "street_shot",
            "default_weight": 1.0,
        },
    ],

    "home_mirror": [
        {
            "id": "home_mirror/bedroom_mirror",
            "prompt": (
                "clean bedroom or walk-in closet interior, "
                "full-length mirror against neutral wall, "
                "soft natural daylight from window, "
                "minimal and clean background, photorealistic"
            ),
            "best_for": {"soft", "formal", "power"},
            "occasion": {"镜自拍", "衣帽间", "全身镜"},
            "photo_mode": "mirror_selfie",
            "default_weight": 1.0,
        },
        {
            "id": "home_mirror/bathroom_mirror",
            "prompt": (
                "modern bathroom with large wall mirror, "
                "warm ambient light, clean neutral tiles, "
                "small potted plant, photorealistic"
            ),
            "best_for": {"soft", "formal", "power"},
            "occasion": {"镜自拍", "全身镜"},
            "photo_mode": "mirror_selfie",
            "default_weight": 1.0,
        },
        {
            "id": "home_mirror/entryway_mirror",
            "prompt": (
                "entryway with floor mirror leaning against wall, "
                "natural light from front door sidelight, "
                "minimalist decor with coat rack, photorealistic"
            ),
            "best_for": {"soft", "formal", "power"},
            "occasion": {"镜自拍", "电梯镜"},
            "photo_mode": "mirror_selfie",
            "default_weight": 1.0,
        },
        {
            "id": "home_mirror/fitting_room",
            "prompt": (
                "boutique fitting room, soft diffused lighting, "
                "clean white walls, full-length mirror, "
                "curtain backdrop, photorealistic"
            ),
            "best_for": {"soft", "formal", "power"},
            "occasion": {"试衣间", "镜自拍"},
            "photo_mode": "mirror_selfie",
            "default_weight": 1.0,
        },
    ],
}

_DEFAULT_SCENE_GROUP = "commute_street"

_DEFAULT_SCENE_DESC = (
    "clean urban street, natural soft daylight, "
    "photorealistic"
)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _pick_mood(items: list) -> str:
    """从单品的 style 标签统计 mood（formal/soft/power），取最多的；全为空时默认 soft"""
    scores = {"formal": 0, "soft": 0, "power": 0}
    for it in items:
        for style in (it.get("style") or []):
            mood = _STYLE_MOOD.get(style)
            if mood:
                scores[mood] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "soft"


# ── 公共接口 ──────────────────────────────────────────────────────────────────

def pick_scene_group(
    items: list,
    occasion: str = None,
    is_weekend: bool = None,
    user_context: dict = None,
) -> str:
    """
    返回 scene_group 字符串。

    优先级：
      1. occasion 命中 _SCENE_HINT（不限白名单）
      2. 单品 style 标签命中 _STRONG_LOCATION_HINTS 时才触发场景
         （度假/通勤/运动/日常等宽松词不在此白名单，避免误判）
      3. mood × weekend 查 _STYLE_TO_DEFAULT_SCENE（不含 home_mirror）
      4. 默认 commute_street

    user_context: 预留参数，未来可传入用户偏好/历史数据，当前不参与逻辑。
    """
    # 1. occasion 子串匹配（所有 _SCENE_HINT 词均生效）
    if occasion:
        for key, group in _SCENE_HINT.items():
            if key in occasion:
                return group

    # 2. 从单品 style 中找场景词（仅强地点词白名单触发）
    for it in items:
        for style in (it.get("style") or []):
            if style in _STRONG_LOCATION_HINTS:
                group = _SCENE_HINT.get(style)
                if group:
                    return group

    # 3. mood × weekend 兜底（home_mirror 不在此表，不会兜底进入）
    if is_weekend is None:
        is_weekend = _date.today().weekday() >= 5

    for it in items:
        for style in (it.get("style") or []):
            mood = _STYLE_MOOD.get(style)
            if mood:
                return _STYLE_TO_DEFAULT_SCENE.get(mood, {}).get(
                    bool(is_weekend), _DEFAULT_SCENE_GROUP
                )

    return _DEFAULT_SCENE_GROUP


def pick_scene_variant(
    scene_group: str,
    style_mood: str = None,
    occasion: str = None,
    exclude_ids: set = None,
    user_context: dict = None,
) -> dict:
    """
    在 scene_group 对应的 variant 列表中按打分选最优，从 top-2 随机。

    打分规则：
      - default_weight（variant 自带基底权重，避免无信号时纯随机）
      - occasion 命中 variant.occasion 关键字：+3.0
      - style_mood 命中 variant.best_for：+2.0
      - 从 top-2 中 random.choice，保留多样性

    exclude_ids: grid 模式下已使用的 variant id 集合（全局唯一 id），避免重复。
    user_context: 预留参数。
    """
    variants = _SCENE_MAP.get(scene_group, [])
    if not variants:
        return {"id": "default", "prompt": _DEFAULT_SCENE_DESC}

    exclude = exclude_ids or set()
    candidates = [v for v in variants if v["id"] not in exclude]
    if not candidates:
        candidates = variants

    scored = []
    for v in candidates:
        score = v.get("default_weight", 1.0)
        if style_mood and style_mood in v.get("best_for", set()):
            score += 2.0
        if occasion:
            for kw in v.get("occasion", set()):
                if kw in occasion:
                    score += 3.0
                    break
        scored.append((score, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_n = min(2, len(scored))
    return random.choice(scored[:top_n])[1]


def pick_scene(
    items: list,
    occasion: str = None,
    is_weekend: bool = None,
    style_mood: str = None,
    exclude_variant_ids: set = None,
    user_context: dict = None,
) -> str:
    """
    旧接口：返回 scene prompt 字符串（str），兼容原有调用方。

    style_mood: 外部传入的 mood（tryon_skill 从 pose_engine 获取），None 时内部计算。
    exclude_variant_ids: grid 模式下避免重复 variant 的 id 集合。
    user_context: 预留参数。
    """
    scene_group = pick_scene_group(items, occasion=occasion, is_weekend=is_weekend, user_context=user_context)
    if style_mood is None:
        style_mood = _pick_mood(items)
    variant = pick_scene_variant(
        scene_group,
        style_mood=style_mood,
        occasion=occasion,
        exclude_ids=exclude_variant_ids,
        user_context=user_context,
    )
    return variant["prompt"]


def pick_scene_with_variant(
    items: list,
    occasion: str = None,
    is_weekend: bool = None,
    style_mood: str = None,
    exclude_variant_ids: set = None,
    user_context: dict = None,
) -> tuple:
    """
    新接口：返回 (prompt: str, variant_id: str)，供需要 variant 去重的调用方使用。

    variant id 全局唯一（格式 scene_group/variant_name），可直接用于 exclude_ids。
    """
    scene_group = pick_scene_group(items, occasion=occasion, is_weekend=is_weekend, user_context=user_context)
    if style_mood is None:
        style_mood = _pick_mood(items)
    variant = pick_scene_variant(
        scene_group,
        style_mood=style_mood,
        occasion=occasion,
        exclude_ids=exclude_variant_ids,
        user_context=user_context,
    )
    return variant["prompt"], variant["id"]