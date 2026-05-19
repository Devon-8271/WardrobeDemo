"""
测试 fashion_router.route() 的接口契约。

用法：
  python test_fashion_router.py

不依赖 Groq API，用规则分类器验证接口格式和兜底逻辑。
"""

import sys


# ── 接口验证 ───────────────────────────────────────────────────────────────────

def assert_route(result: dict, label: str):
    assert isinstance(result, dict),              f"[{label}] 应返回 dict"
    assert "key" in result,                       f"[{label}] 缺少 key"
    assert "input" in result,                     f"[{label}] 缺少 input"
    assert isinstance(result["key"], str),        f"[{label}] key 应为 str"
    valid_keys = {"recommend", "swap_item", "quick_tryon", "wardrobe_query", "save_look", "unknown"}
    assert result["key"] in valid_keys,           f"[{label}] 非法 key: {result['key']}"


# ── 测试用例 ───────────────────────────────────────────────────────────────────

CASES = [
    # (输入, 期望 key)
    ("今天穿什么",                 "recommend"),
    ("帮我搭一套明天上班穿的",     "recommend"),
    ("天气这么冷穿什么好",         "recommend"),
    ("换条深色牛仔裤试试",         "swap_item"),
    ("换双乐福鞋",                 "swap_item"),
    ("上衣换成白色的",             "swap_item"),
    ("帮我试穿这件",               "quick_tryon"),
    ("我想试一下这个",             "quick_tryon"),
    ("我衣橱里有什么外套",         "wardrobe_query"),
    ("查一下我有几件裙子",         "wardrobe_query"),
    ("保存这套搭配",               "save_look"),
    ("记录今天的穿搭",             "save_look"),
]


def test_return_format(route_fn):
    """所有输入都返回合法格式"""
    for text, _ in CASES:
        result = route_fn(text)
        assert_route(result, text)
    print("  ✓ 所有输入返回合法格式")


def test_rule_classify_accuracy(route_fn):
    """规则分类器准确率 ≥ 80%"""
    correct = 0
    wrong   = []
    for text, expected in CASES:
        result = route_fn(text)
        if result["key"] == expected:
            correct += 1
        else:
            wrong.append(f"  输入「{text}」→ 期望 {expected}，实际 {result['key']}")
    accuracy = correct / len(CASES)
    if wrong:
        print(f"  分类错误：")
        for w in wrong:
            print(w)
    assert accuracy >= 0.8, f"准确率 {accuracy:.0%} 低于 80%"
    print(f"  ✓ 规则分类准确率 {accuracy:.0%} ({correct}/{len(CASES)})")


def test_unknown_fallback(route_fn):
    """无法识别的输入返回 unknown，不抛异常"""
    result = route_fn("balabala 完全无关的话")
    assert result["key"] in {"unknown", "recommend"}, "无法识别应降级到 unknown"
    print("  ✓ 未知输入正常降级")


def test_input_preserved(route_fn):
    """result['input'] 等于原始输入"""
    text   = "换条黑色裤子"
    result = route_fn(text)
    assert result["input"] == text, "input 字段应保留原始输入"
    print("  ✓ 原始输入保留在 result['input']")


def test_swap_item_extracts_category(route_fn):
    """swap_item 时尽量提取目标品类"""
    cases = [
        ("换条裤子",  "下装"),
        ("换双鞋",    "鞋履"),
        ("换件外套",  "外套"),
        ("换件上衣",  "上装"),
    ]
    for text, expected_cat in cases:
        result = route_fn(text)
        if result["key"] == "swap_item":
            cat = result.get("category")
            assert cat == expected_cat, f"「{text}」应提取 category={expected_cat}，实际 {cat}"
    print("  ✓ swap_item 品类提取正确")


# ── 入口 ───────────────────────────────────────────────────────────────────────

def run():
    try:
        from fashion_router import route
    except ImportError:
        print("⚠️  fashion_router.py 尚未实现")
        sys.exit(1)

    tests = [
        ("test_return_format",              lambda: test_return_format(route)),
        ("test_rule_classify_accuracy",     lambda: test_rule_classify_accuracy(route)),
        ("test_unknown_fallback",           lambda: test_unknown_fallback(route)),
        ("test_input_preserved",            lambda: test_input_preserved(route)),
        ("test_swap_item_extracts_category",lambda: test_swap_item_extracts_category(route)),
    ]

    print("\n=== test_fashion_router ===")
    failed = 0
    for name, t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {name} 异常: {e}")
            failed += 1

    print(f"\n{'全部通过' if not failed else f'{failed} 个失败'} ({len(tests) - failed}/{len(tests)})\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
