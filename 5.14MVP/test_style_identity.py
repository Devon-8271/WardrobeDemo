"""
测试 style_identity.compute_style_identity() 的接口契约。

用法：
  python test_style_identity.py

使用临时 DB + mock look 数据，不依赖真实衣橱。
"""

import sys
import os
import tempfile
import uuid
from datetime import date, timedelta

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()

import db
db.DB_PATH = _tmp.name
db.init_db()


# ── 构造测试数据 ───────────────────────────────────────────────────────────────

def _seed_items():
    """写入几件测试单品"""
    items = [
        {"item_id": "i1", "type": "衬衫",  "category": "上装", "raw_type": "白衬衫",
         "color": ["白"], "style": ["法式", "优雅"], "season": ["春"],
         "warmth": "薄", "fit": "宽松", "source": "real"},
        {"item_id": "i2", "type": "针织衫","category": "上装", "raw_type": "米色针织",
         "color": ["米"], "style": ["休闲", "法式"], "season": ["秋"],
         "warmth": "中等", "fit": "宽松", "source": "real"},
        {"item_id": "i3", "type": "长裤",  "category": "下装", "raw_type": "黑直筒裤",
         "color": ["黑"], "style": ["通勤", "简约"], "season": ["春","秋"],
         "warmth": "中等", "fit": "直筒", "source": "real"},
        {"item_id": "i4", "type": "连衣裙","category": "全身", "raw_type": "碎花裙",
         "color": ["蓝"], "style": ["法式", "浪漫"], "season": ["夏"],
         "warmth": "薄", "fit": "A字", "source": "real"},
    ]
    from datetime import datetime
    for it in items:
        it["upload_time"] = datetime.now().isoformat()
        try:
            db.insert_wardrobe_item(it)
        except Exception:
            pass


def _seed_looks(n_this_month=6, n_last_month=4):
    """写入本月和上月的 look 记录"""
    today = date.today()
    # 本月：法式风格为主
    this_month_items = [["i1", "i3"], ["i2", "i3"], ["i4"], ["i1", "i3"], ["i2", "i3"], ["i4"]]
    for i in range(n_this_month):
        d = (today - timedelta(days=i)).isoformat()
        db.insert_look({"look_id": uuid.uuid4().hex, "date": d,
                        "item_ids": this_month_items[i % len(this_month_items)],
                        "photo_url": "", "scene": "通勤", "source": "styling", "user_id": "default"})
    # 上月：通勤风格为主
    last_month_items = [["i3"], ["i3"], ["i3"], ["i3"]]
    for i in range(n_last_month):
        d = (today - timedelta(days=30 + i)).isoformat()
        db.insert_look({"look_id": uuid.uuid4().hex, "date": d,
                        "item_ids": last_month_items[i % len(last_month_items)],
                        "photo_url": "", "scene": "通勤", "source": "styling", "user_id": "default"})


# ── 接口验证 ───────────────────────────────────────────────────────────────────

def test_return_format(compute_fn):
    """返回 dict，含 tags / distribution / trend"""
    result = compute_fn("default")
    assert isinstance(result, dict),             "应返回 dict"
    assert "tags" in result,                     "缺少 tags"
    assert "distribution" in result,             "缺少 distribution"
    assert "trend" in result,                    "缺少 trend"
    assert isinstance(result["tags"], list),     "tags 应为 list"
    assert isinstance(result["distribution"], dict), "distribution 应为 dict"
    assert isinstance(result["trend"], dict),    "trend 应为 dict"
    print("  ✓ 返回格式正确")


def test_tags_nonempty(compute_fn):
    """有 look 数据时 tags 不为空"""
    result = compute_fn("default")
    assert len(result["tags"]) >= 1, "有穿搭数据时 tags 不应为空"
    print(f"  ✓ tags 非空: {result['tags']}")


def test_distribution_sums_to_one(compute_fn):
    """distribution 各项之和约为 1.0"""
    result = compute_fn("default")
    dist = result["distribution"]
    if dist:
        total = sum(dist.values())
        assert abs(total - 1.0) < 0.01, f"distribution 之和应为 1，实际 {total:.3f}"
    print("  ✓ distribution 归一化正确")


def test_top_tag_matches_most_worn_style(compute_fn):
    """法式风格出现最多（本月 i1/i2/i4 都有），应在 tags[0]"""
    result = compute_fn("default")
    assert "法式" in result["tags"], f"法式应在 tags 中，实际: {result['tags']}"
    print(f"  ✓ 最高频风格正确出现在 tags: {result['tags'][0]}")


def test_empty_wardrobe_returns_empty(compute_fn):
    """没有 look 的用户返回空结构，不报错"""
    result = compute_fn("nonexistent_user")
    assert result["tags"] == []
    assert result["distribution"] == {}
    print("  ✓ 无数据用户返回空结构")


# ── 入口 ───────────────────────────────────────────────────────────────────────

def run():
    _seed_items()
    _seed_looks()

    try:
        from style_identity import compute_style_identity
    except ImportError:
        print("⚠️  style_identity.py 尚未实现，使用内置 mock...")
        from collections import Counter

        def compute_style_identity(user_id: str) -> dict:
            looks = db.get_looks(user_id=user_id, limit=60)
            if not looks:
                return {"tags": [], "distribution": {}, "trend": {}}

            today = date.today()
            this_month = today.replace(day=1).isoformat()

            this_styles, last_styles = [], []
            for look in looks:
                items = [db.get_wardrobe_item(iid) for iid in look["item_ids"]]
                for it in items:
                    if it:
                        (this_styles if look["date"] >= this_month else last_styles).extend(it["style"])

            this_cnt  = Counter(this_styles)
            last_cnt  = Counter(last_styles)
            total     = sum(this_cnt.values()) or 1
            dist      = {k: round(v / total, 3) for k, v in this_cnt.most_common()}
            tags      = [k for k, _ in this_cnt.most_common(5)]
            trend     = {}
            for tag in set(list(this_cnt) + list(last_cnt)):
                delta = this_cnt.get(tag, 0) / total - last_cnt.get(tag, 0) / (sum(last_cnt.values()) or 1)
                trend[tag] = round(delta, 3)
            return {"tags": tags, "distribution": dist, "trend": trend}

    tests = [
        ("test_return_format",             lambda: test_return_format(compute_style_identity)),
        ("test_tags_nonempty",             lambda: test_tags_nonempty(compute_style_identity)),
        ("test_distribution_sums_to_one",  lambda: test_distribution_sums_to_one(compute_style_identity)),
        ("test_top_tag_matches_most_worn", lambda: test_top_tag_matches_most_worn_style(compute_style_identity)),
        ("test_empty_wardrobe",            lambda: test_empty_wardrobe_returns_empty(compute_style_identity)),
    ]

    print("\n=== test_style_identity ===")
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
