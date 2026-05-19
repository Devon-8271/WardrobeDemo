"""
测试 fashion_dispatch.dispatch() 的接口契约。

用法：
  python test_fashion_dispatch.py

mock image2 和 Groq，不依赖真实服务。
"""

import sys
import os
import tempfile
import unittest.mock as mock
from PIL import Image

import db

# 用临时 DB
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

# 写入测试单品
from datetime import datetime
_items = [
    {"item_id": "top1", "category": "上装", "type": "针织衫", "raw_type": "针织衫",
     "color": ["米色"], "style": ["法式"], "season": ["秋"], "warmth": "中等",
     "fit": "宽松", "description": "米色针织衫", "image_url": "", "source": "real",
     "upload_time": datetime.now().isoformat()},
    {"item_id": "bot1", "category": "下装", "type": "长裤", "raw_type": "长裤",
     "color": ["黑色"], "style": ["通勤"], "season": ["春","秋"], "warmth": "中等",
     "fit": "直筒", "description": "黑色直筒裤", "image_url": "", "source": "real",
     "upload_time": datetime.now().isoformat()},
]
for it in _items:
    try: db.insert_wardrobe_item(it)
    except: pass

# 用户 profile（含 photo）
db.upsert_user_profile({
    "user_id": "default", "photo_url": "test/step1.png",
    "height": "", "body_type": "", "skin_tone": "",
    "style_preference": [], "temp_offset": 0, "personal_color": "",
    "upload_time": datetime.now().isoformat(),
})

MOCK_WEATHER  = {"temp_c": 20, "description": "晴"}
MOCK_OUTFITS  = [{"item_ids": ["top1", "bot1"], "style_tags": ["法式"], "caption": "清爽法式"}]
MOCK_IMG_PATH = "images/grid/fake.png"


# ── 接口验证 ───────────────────────────────────────────────────────────────────

def assert_response(resp: dict, label: str):
    assert isinstance(resp, dict),           f"[{label}] 应返回 dict"
    assert "action"  in resp,                f"[{label}] 缺少 action"
    assert "payload" in resp,                f"[{label}] 缺少 payload"
    assert "message" in resp,                f"[{label}] 缺少 message"
    assert isinstance(resp["message"], str), f"[{label}] message 应为 str"


# ── 测试 ───────────────────────────────────────────────────────────────────────

def test_recommend_returns_outfits(dispatch_fn):
    """recommend 路由返回 outfits 列表和 images 列表"""
    fake_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    Image.new("RGB", (100, 100), "white").save(fake_img.name)

    with mock.patch("outfit_recommender.recommend_outfits", return_value=MOCK_OUTFITS), \
         mock.patch("outfit_generator.generate_outfit_grid", return_value=[fake_img.name]), \
         mock.patch("fashion_router._llm_classify", return_value="recommend"):
        resp = dispatch_fn("今天穿什么", weather=MOCK_WEATHER)

    assert_response(resp, "recommend")
    assert resp["action"]            == "recommend"
    assert "outfits" in resp["payload"]
    assert "images"  in resp["payload"]
    assert len(resp["payload"]["images"]) >= 1
    os.unlink(fake_img.name)
    print("  ✓ recommend 返回 outfits + images")


def test_swap_item_with_context(dispatch_fn):
    """swap_item 有 context 时调用 regenerate，返回新图路径"""
    fake_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    Image.new("RGB", (100, 100), "white").save(fake_img.name)

    context = {"current_item_ids": ["top1", "bot1"], "user_photo": "test/step1.png"}
    with mock.patch("outfit_generator.regenerate_single_outfit", return_value=fake_img.name), \
         mock.patch("fashion_router._llm_classify", return_value="swap_item"):
        resp = dispatch_fn("换条黑色裤子", context=context)

    assert_response(resp, "swap_item")
    assert resp["action"] == "swap_item"
    assert "image" in resp["payload"]
    os.unlink(fake_img.name)
    print("  ✓ swap_item 有 context 时返回新图")


def test_swap_item_without_context(dispatch_fn):
    """swap_item 无 context 时降级为 recommend，不报错"""
    with mock.patch("outfit_recommender.recommend_outfits", return_value=MOCK_OUTFITS), \
         mock.patch("outfit_generator.generate_outfit_grid", return_value=[MOCK_IMG_PATH]), \
         mock.patch("fashion_router._llm_classify", return_value="swap_item"):
        resp = dispatch_fn("换条裤子")   # 无 context

    assert_response(resp, "swap_item_no_context")
    assert resp["action"] in ("recommend", "swap_item")
    print("  ✓ swap_item 无 context 时正常降级，不抛异常")


def test_wardrobe_query_returns_items(dispatch_fn):
    """wardrobe_query 返回衣橱单品列表"""
    with mock.patch("fashion_router._llm_classify", return_value="wardrobe_query"):
        resp = dispatch_fn("我衣橱里有什么")

    assert_response(resp, "wardrobe_query")
    assert resp["action"]           == "wardrobe_query"
    assert "items" in resp["payload"]
    assert isinstance(resp["payload"]["items"], list)
    print(f"  ✓ wardrobe_query 返回 {len(resp['payload']['items'])} 件单品")


def test_save_look_with_context(dispatch_fn):
    """save_look 有 context 时保存并返回 look_id"""
    context = {"current_item_ids": ["top1", "bot1"], "current_photo": "images/fake.png"}
    with mock.patch("fashion_router._llm_classify", return_value="save_look"):
        resp = dispatch_fn("保存这套", context=context)

    assert_response(resp, "save_look")
    assert resp["action"]           == "save_look"
    assert "look_id" in resp["payload"]
    assert resp["payload"]["look_id"]
    print("  ✓ save_look 返回 look_id")


def test_unknown_returns_gracefully(dispatch_fn):
    """unknown 返回提示，不抛异常"""
    with mock.patch("fashion_router._llm_classify", return_value="unknown"):
        resp = dispatch_fn("balabala")

    assert_response(resp, "unknown")
    assert len(resp["message"]) > 0
    print("  ✓ unknown 返回提示信息")


# ── 入口 ───────────────────────────────────────────────────────────────────────

def run():
    try:
        from fashion_dispatch import dispatch
    except ImportError:
        print("⚠️  fashion_dispatch.py 尚未实现")
        sys.exit(1)

    tests = [
        ("test_recommend_returns_outfits",   lambda: test_recommend_returns_outfits(dispatch)),
        ("test_swap_item_with_context",      lambda: test_swap_item_with_context(dispatch)),
        ("test_swap_item_without_context",   lambda: test_swap_item_without_context(dispatch)),
        ("test_wardrobe_query_returns_items",lambda: test_wardrobe_query_returns_items(dispatch)),
        ("test_save_look_with_context",      lambda: test_save_look_with_context(dispatch)),
        ("test_unknown_returns_gracefully",  lambda: test_unknown_returns_gracefully(dispatch)),
    ]

    print("\n=== test_fashion_dispatch ===")
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

    os.unlink(_tmp.name)
    print(f"\n{'全部通过' if not failed else f'{failed} 个失败'} ({len(tests) - failed}/{len(tests)})\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
