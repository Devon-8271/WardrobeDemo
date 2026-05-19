"""
测试 look_manager.save_look / get_looks 的接口契约。

用法：
  python test_look_manager.py

使用临时 SQLite DB，不污染 wardrobe.db。
"""

import sys
import os
import tempfile

# 临时 DB，不碰真实数据
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["WARDROBE_DB_PATH"] = _tmp.name

import db
db.DB_PATH = _tmp.name
db.init_db()


# ── 接口验证 ───────────────────────────────────────────────────────────────────

def assert_look(look: dict, idx: int):
    for field in ["look_id", "date", "item_ids", "photo_url", "scene", "source"]:
        assert field in look, f"look[{idx}] 缺少字段 {field!r}"
    assert isinstance(look["item_ids"], list), f"look[{idx}].item_ids 应为 list"
    assert isinstance(look["look_id"], str) and look["look_id"], f"look[{idx}].look_id 不能为空"


def test_save_returns_look_id(save_fn):
    """save_look 返回非空字符串 look_id"""
    look_id = save_fn(
        user_id="default",
        item_ids=["a1", "b1"],
        photo_url="images/test.png",
        scene="通勤",
        source="styling",
    )
    assert isinstance(look_id, str) and look_id, "save_look 应返回非空 look_id"
    print("  ✓ save_look 返回 look_id")


def test_get_looks_format(save_fn, get_fn):
    """get_looks 返回 list[dict]，字段完整"""
    save_fn(user_id="u1", item_ids=["a1", "b1"], source="styling")
    save_fn(user_id="u1", item_ids=["a2", "b2"], source="ootd")
    result = get_fn(user_id="u1")
    assert isinstance(result, list) and len(result) >= 2
    for i, look in enumerate(result):
        assert_look(look, i)
    print("  ✓ get_looks 格式正确")


def test_scene_filter(save_fn, get_fn):
    """scene 参数过滤生效"""
    save_fn(user_id="u2", item_ids=["x1"], scene="约会",  source="styling")
    save_fn(user_id="u2", item_ids=["x2"], scene="通勤",  source="styling")
    save_fn(user_id="u2", item_ids=["x3"], scene="约会",  source="styling")
    result = get_fn(user_id="u2", scene="约会")
    assert all(l["scene"] == "约会" for l in result), "过滤后应只含约会"
    assert len(result) == 2
    print("  ✓ scene 过滤生效")


def test_ootd_no_photo(save_fn, get_fn):
    """OOTD 来源 photo_url 为空，字段依然存在"""
    look_id = save_fn(user_id="u3", item_ids=["c1"], source="ootd", photo_url=None)
    result = get_fn(user_id="u3")
    match = [l for l in result if l["look_id"] == look_id]
    assert match, "刚保存的 look 应能查到"
    assert match[0]["photo_url"] == "" or match[0]["photo_url"] is None
    print("  ✓ OOTD 无原图（photo_url 为空）正常存取")


def test_limit(save_fn, get_fn):
    """limit 参数生效"""
    for i in range(5):
        save_fn(user_id="u4", item_ids=[f"item{i}"], source="manual")
    result = get_fn(user_id="u4", limit=3)
    assert len(result) <= 3
    print("  ✓ limit 参数生效")


# ── 入口 ───────────────────────────────────────────────────────────────────────

def run():
    try:
        from look_manager import save_look, get_looks
    except ImportError:
        print("⚠️  look_manager.py 尚未实现，使用内置 mock...")
        import uuid
        from datetime import date

        def save_look(user_id, item_ids, photo_url=None, scene=None, source="manual"):
            look_id = uuid.uuid4().hex
            db.insert_look({
                "look_id":  look_id,
                "date":     date.today().isoformat(),
                "item_ids": item_ids,
                "photo_url": photo_url or "",
                "scene":    scene or "",
                "source":   source,
                "user_id":  user_id,
            })
            return look_id

        def get_looks(user_id, scene=None, limit=30):
            return db.get_looks(user_id=user_id, scene=scene, limit=limit)

    tests = [
        lambda: test_save_returns_look_id(save_look),
        lambda: test_get_looks_format(save_look, get_looks),
        lambda: test_scene_filter(save_look, get_looks),
        lambda: test_ootd_no_photo(save_look, get_looks),
        lambda: test_limit(save_look, get_looks),
    ]
    names = [
        "test_save_returns_look_id",
        "test_get_looks_format",
        "test_scene_filter",
        "test_ootd_no_photo",
        "test_limit",
    ]

    print("\n=== test_look_manager ===")
    failed = 0
    for name, t in zip(names, tests):
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
