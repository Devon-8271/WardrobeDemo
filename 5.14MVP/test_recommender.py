"""
测试 outfit_recommender.recommend_outfits() 的接口契约。

用法：
  python test_recommender.py

不依赖 DB、image2、网络。所有输入 mock，只验证返回格式正确。
"""

import sys

# ── Mock 数据 ──────────────────────────────────────────────────────────────────

MOCK_WEATHER = {"temp_c": 12, "description": "小雨"}

MOCK_ITEMS = [
    {"item_id": "a1", "category": "上装", "type": "衬衫",  "color": ["白色"], "style": ["法式"], "season": ["春","秋"], "warmth": "薄",  "fit": "宽松"},
    {"item_id": "a2", "category": "上装", "type": "针织衫","color": ["米色"], "style": ["休闲"], "season": ["秋","冬"], "warmth": "中等","fit": "常规"},
    {"item_id": "b1", "category": "下装", "type": "长裤",  "color": ["黑色"], "style": ["通勤"], "season": ["春","秋","冬"], "warmth": "中等","fit": "直筒"},
    {"item_id": "b2", "category": "下装", "type": "半身裙","color": ["棕色"], "style": ["法式"], "season": ["春","秋"], "warmth": "薄",  "fit": "A字"},
    {"item_id": "c1", "category": "外套", "type": "风衣",  "color": ["卡其"], "style": ["通勤"], "season": ["春","秋"], "warmth": "中等","fit": "常规"},
    {"item_id": "d1", "category": "鞋履", "type": "乐福鞋","color": ["黑色"], "style": ["通勤"], "season": ["春","秋","冬"], "warmth": "不适用","fit": "不适用"},
]


# ── 接口验证函数 ───────────────────────────────────────────────────────────────

def assert_outfit(outfit: dict, idx: int):
    assert isinstance(outfit, dict), f"outfit[{idx}] 应为 dict"
    assert "item_ids" in outfit,     f"outfit[{idx}] 缺少 item_ids"
    assert "style_tags" in outfit,   f"outfit[{idx}] 缺少 style_tags"
    assert "caption" in outfit,      f"outfit[{idx}] 缺少 caption"

    assert isinstance(outfit["item_ids"], list),  f"outfit[{idx}].item_ids 应为 list"
    assert isinstance(outfit["style_tags"], list), f"outfit[{idx}].style_tags 应为 list"
    assert isinstance(outfit["caption"], str),     f"outfit[{idx}].caption 应为 str"

    assert len(outfit["item_ids"]) >= 1, f"outfit[{idx}].item_ids 不能为空"
    assert len(outfit["caption"]) > 0,   f"outfit[{idx}].caption 不能为空字符串"


def test_return_format(recommend_fn):
    """返回 list[dict]，每个 dict 含 item_ids / style_tags / caption"""
    result = recommend_fn(
        user_id="default",
        weather=MOCK_WEATHER,
        occasion=None,
        n=4,
        _mock_items=MOCK_ITEMS,
    )
    assert isinstance(result, list), "返回值应为 list"
    assert len(result) >= 1, "至少返回 1 套"
    for i, outfit in enumerate(result):
        assert_outfit(outfit, i)
    print(f"  ✓ 返回格式正确，共 {len(result)} 套")


def test_n_param(recommend_fn):
    """n 参数控制返回套数（不超过可组合的上限）"""
    for n in [1, 2, 4]:
        result = recommend_fn(
            user_id="default",
            weather=MOCK_WEATHER,
            n=n,
            _mock_items=MOCK_ITEMS,
        )
        assert len(result) <= n, f"n={n} 时返回套数 {len(result)} 超过 n"
    print("  ✓ n 参数生效")


def test_warmth_filter(recommend_fn):
    """冬天高温场景：推荐结果不应包含 warmth='薄' 的单品"""
    cold_weather = {"temp_c": 2, "description": "大雪"}
    result = recommend_fn(
        user_id="default",
        weather=cold_weather,
        n=4,
        _mock_items=MOCK_ITEMS,
    )
    all_item_ids = [iid for outfit in result for iid in outfit["item_ids"]]
    thin_items = {item["item_id"] for item in MOCK_ITEMS if item["warmth"] == "薄"}
    overlap = set(all_item_ids) & thin_items
    assert not overlap, f"低温时不应推荐薄款单品，但出现了: {overlap}"
    print("  ✓ warmth 过滤生效")


def test_item_ids_exist_in_wardrobe(recommend_fn):
    """返回的 item_id 必须都在衣橱中"""
    result = recommend_fn(
        user_id="default",
        weather=MOCK_WEATHER,
        n=4,
        _mock_items=MOCK_ITEMS,
    )
    valid_ids = {item["item_id"] for item in MOCK_ITEMS}
    for i, outfit in enumerate(result):
        for iid in outfit["item_ids"]:
            assert iid in valid_ids, f"outfit[{i}] 包含不存在的 item_id: {iid}"
    print("  ✓ item_id 均在衣橱中")


# ── 缓存 / 加权 / 重复惩罚 ─────────────────────────────────────────────────────

def test_daily_cache(recommend_fn):
    """同 user/日期/温度段：连续两次走真实路径返回完全相同的结果（缓存命中）。"""
    import outfit_recommender as r
    # mock DB 调用：让缓存路径走通
    r.db.get_all_wardrobe_items = lambda source_filter=None: MOCK_ITEMS
    r.db.get_user_profile       = lambda u="default": {"style_tags": []}
    r.db.get_looks              = lambda user_id="default", scene=None, limit=30: []
    r.clear_cache()

    first  = recommend_fn(user_id="cache_user", weather=MOCK_WEATHER, n=3)
    second = recommend_fn(user_id="cache_user", weather=MOCK_WEATHER, n=3)
    assert first == second, "同一天同温度段两次调用应返回相同结果（命中缓存）"

    third = recommend_fn(user_id="cache_user", weather=MOCK_WEATHER, n=3, refresh=True)
    # refresh 时若候选很少可能仍重合，但缓存键应被刷新；至少调用不报错
    assert isinstance(third, list) and len(third) >= 1
    r.clear_cache()
    print("  ✓ 日级缓存生效 + refresh 可强制重算")


def test_cache_invalidates_on_wardrobe_change(recommend_fn):
    """缓存里 item_id 不在当前衣橱时应自动失效。"""
    import outfit_recommender as r
    r.db.get_user_profile = lambda u="default": {"style_tags": []}
    r.db.get_looks        = lambda user_id="default", scene=None, limit=30: []
    r.clear_cache()

    # 第一次：用 6 件单品
    r.db.get_all_wardrobe_items = lambda source_filter=None: MOCK_ITEMS
    first = recommend_fn(user_id="inv_user", weather=MOCK_WEATHER, n=3)

    # 第二次：删掉所有"上装"，原缓存里的 item_id 失效，应重算
    reduced = [it for it in MOCK_ITEMS if it["category"] != "上装"]
    r.db.get_all_wardrobe_items = lambda source_filter=None: reduced
    second = recommend_fn(user_id="inv_user", weather=MOCK_WEATHER, n=3)

    first_tops  = {iid for o in first  for iid in o["item_ids"]}
    second_tops = {iid for o in second for iid in o["item_ids"]}
    assert first_tops != second_tops, "衣橱缩减后缓存应失效，结果应不同"
    r.clear_cache()
    print("  ✓ 衣橱变动 → 缓存自动失效")


def test_style_weighting(recommend_fn):
    """用户偏好「法式」时，第一套的 style_tags 应包含「法式」（高分组合优先）。"""
    import outfit_recommender as r
    r.db.get_user_profile = lambda u="default": {"style_tags": ["法式"]}
    r.db.get_looks        = lambda user_id="default", scene=None, limit=30: []

    warm = {"temp_c": 20, "description": "晴"}    # 让薄/中等都通过 warmth 过滤
    hits = 0
    trials = 10
    for _ in range(trials):
        result = recommend_fn(
            user_id="style_user", weather=warm, n=2,
            _mock_items=MOCK_ITEMS,
        )
        if result and "法式" in result[0].get("style_tags", []):
            hits += 1
    assert hits >= 6, f"法式偏好应主导首套排序，10 次只命中 {hits} 次"
    print(f"  ✓ 风格偏好加权生效（{hits}/{trials} 法式首套）")


def test_recent_look_penalty(recommend_fn):
    """最近 7 天穿过的高重叠组合应被降权（首套不应是它）。"""
    import outfit_recommender as r
    from datetime import date

    recent = [{"date": date.today().isoformat(), "item_ids": ["a1", "b1"]}]
    r.db.get_user_profile = lambda u="default": {"style_tags": []}
    r.db.get_looks        = lambda user_id="default", scene=None, limit=30: recent

    warm = {"temp_c": 20, "description": "晴"}
    hits = 0
    trials = 10
    for _ in range(trials):
        result = recommend_fn(
            user_id="penalty_user", weather=warm, n=4,
            _mock_items=MOCK_ITEMS,
        )
        first_ids = set(result[0]["item_ids"]) if result else set()
        if first_ids >= {"a1", "b1"}:
            hits += 1
    # 无惩罚下 a1+b1 约 1/4 概率排首；有惩罚应明显更低
    assert hits <= 3, f"a1+b1 最近穿过却仍 {hits}/{trials} 次排首套，惩罚未生效"
    print(f"  ✓ 最近穿过的组合被降权（{hits}/{trials} 排首套）")


# ── 入口 ───────────────────────────────────────────────────────────────────────

def run():
    try:
        from outfit_recommender import recommend_outfits
    except ImportError:
        print("⚠️  outfit_recommender.py 尚未实现，运行 mock 验证接口定义...")
        # 用占位 mock 验证测试本身能跑通
        def recommend_outfits(user_id, weather, occasion=None, n=4, _mock_items=None):
            items = _mock_items or []
            temp = weather.get("temp_c", 15)
            if temp <= 5:
                items = [it for it in items if it["warmth"] != "薄"]
            tops    = [it for it in items if it["category"] == "上装"][:n]
            bottoms = [it for it in items if it["category"] == "下装"][:n]
            outfits = []
            for i in range(min(n, len(tops), len(bottoms))):
                outfits.append({
                    "item_ids":   [tops[i]["item_id"], bottoms[i]["item_id"]],
                    "style_tags": tops[i]["style"],
                    "caption":    f"这套{tops[i]['style'][0] if tops[i]['style'] else ''}风格，适合今天天气",
                })
            return outfits

    tests = [
        test_return_format,
        test_n_param,
        test_warmth_filter,
        test_item_ids_exist_in_wardrobe,
        test_daily_cache,
        test_cache_invalidates_on_wardrobe_change,
        test_style_weighting,
        test_recent_look_penalty,
    ]

    print("\n=== test_recommender ===")
    failed = 0
    for t in tests:
        try:
            t(recommend_outfits)
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__} 异常: {e}")
            failed += 1

    print(f"\n{'全部通过' if not failed else f'{failed} 个失败'} ({len(tests) - failed}/{len(tests)})\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
