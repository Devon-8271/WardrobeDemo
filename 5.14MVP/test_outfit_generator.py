"""
测试 outfit_generator 的接口契约。

用法：
  python test_outfit_generator.py

mock image2_client.generate，不需要真实服务。
用 Pillow 创建假拼图验证裁切逻辑。
"""

import sys
import os
import tempfile
import unittest.mock as mock
from PIL import Image

# ── 构造假拼图图片 ─────────────────────────────────────────────────────────────

def _make_fake_grid(cols: int, rows: int, cell_w=256, cell_h=384) -> str:
    """创建一张带颜色分块的假拼图，用于验证裁切坐标正确。"""
    colors = ["#FADADD", "#DAE8FC", "#D5E8D4", "#FFE6CC", "#E1D5E7", "#FFF2CC"]
    img = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    for row in range(rows):
        for col in range(cols):
            color = colors[(row * cols + col) % len(colors)]
            block = Image.new("RGB", (cell_w - 4, cell_h - 4), color)
            img.paste(block, (col * cell_w + 2, row * cell_h + 2))
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    return tmp.name


MOCK_OUTFITS = [
    {"item_ids": ["a1", "b1"], "style_tags": ["法式"], "caption": "套装1"},
    {"item_ids": ["a2", "b2"], "style_tags": ["休闲"], "caption": "套装2"},
    {"item_ids": ["a1", "b2"], "style_tags": ["通勤"], "caption": "套装3"},
    {"item_ids": ["a2", "b1"], "style_tags": ["法式"], "caption": "套装4"},
]

MOCK_ITEMS = {
    "a1": {"image_url": "images/a1.jpg"},
    "a2": {"image_url": "images/a2.jpg"},
    "b1": {"image_url": "images/b1.jpg"},
    "b2": {"image_url": "images/b2.jpg"},
}


# ── 测试 ───────────────────────────────────────────────────────────────────────

def test_grid_returns_n_paths(generate_fn):
    """generate_outfit_grid 返回 list[str]，长度 == outfit 数量"""
    fake_grid = _make_fake_grid(cols=2, rows=2)
    with mock.patch("image2_client.generate", return_value=fake_grid), \
         mock.patch("image2_client.healthz", return_value=True):
        result = generate_fn(
            user_photo="test/step1.png",
            outfits=MOCK_OUTFITS,
            layout="2x2",
        )
    assert isinstance(result, list),         "应返回 list"
    assert len(result) == len(MOCK_OUTFITS), f"应返回 {len(MOCK_OUTFITS)} 张，实际 {len(result)}"
    for p in result:
        assert isinstance(p, str) and p,     "每个元素应为非空字符串路径"
    os.unlink(fake_grid)
    print(f"  ✓ 返回 {len(result)} 张效果图路径")


def test_cropped_files_exist(generate_fn):
    """裁切后的文件实际存在于磁盘"""
    fake_grid = _make_fake_grid(cols=2, rows=2)
    with mock.patch("image2_client.generate", return_value=fake_grid), \
         mock.patch("image2_client.healthz", return_value=True):
        result = generate_fn(
            user_photo="test/step1.png",
            outfits=MOCK_OUTFITS[:4],
            layout="2x2",
        )
    for p in result:
        assert os.path.isfile(p), f"裁切文件不存在: {p}"
        img = Image.open(p)
        assert img.size[0] > 0 and img.size[1] > 0
    # 清理
    for p in result:
        try: os.unlink(p)
        except: pass
    os.unlink(fake_grid)
    print("  ✓ 裁切文件存在且可读")


def test_cropped_size_correct(generate_fn):
    """每张裁切图的尺寸约为原图的 1/格数"""
    cols, rows, cell_w, cell_h = 2, 2, 256, 384
    fake_grid = _make_fake_grid(cols=cols, rows=rows, cell_w=cell_w, cell_h=cell_h)
    with mock.patch("image2_client.generate", return_value=fake_grid), \
         mock.patch("image2_client.healthz", return_value=True):
        result = generate_fn(
            user_photo="test/step1.png",
            outfits=MOCK_OUTFITS[:4],
            layout="2x2",
        )
    for p in result:
        img = Image.open(p)
        assert img.width  == cell_w, f"宽度应为 {cell_w}，实际 {img.width}"
        assert img.height == cell_h, f"高度应为 {cell_h}，实际 {img.height}"
    for p in result:
        try: os.unlink(p)
        except: pass
    os.unlink(fake_grid)
    print(f"  ✓ 裁切尺寸正确 ({cell_w}x{cell_h})")


def test_regenerate_returns_single_path(regenerate_fn):
    """regenerate_single_outfit 返回单个非空路径"""
    fake_img = _make_fake_grid(cols=1, rows=1, cell_w=256, cell_h=384)
    with mock.patch("image2_client.generate", return_value=fake_img), \
         mock.patch("image2_client.healthz", return_value=True):
        result = regenerate_fn(
            user_photo="test/step1.png",
            item_ids=["a1", "b1"],
        )
    assert isinstance(result, str) and result, "应返回非空字符串路径"
    os.unlink(fake_img)
    print("  ✓ regenerate_single_outfit 返回单个路径")


def test_image2_unavailable_raises(generate_fn):
    """image2 不可达时抛出异常，不返回空列表"""
    with mock.patch("image2_client.healthz", return_value=False):
        try:
            generate_fn(user_photo="test/step1.png", outfits=MOCK_OUTFITS)
            assert False, "image2 不可达时应抛出异常"
        except RuntimeError:
            pass
    print("  ✓ image2 不可达时抛出 RuntimeError")


# ── 入口 ───────────────────────────────────────────────────────────────────────

def run():
    try:
        from outfit_generator import generate_outfit_grid, regenerate_single_outfit
    except ImportError:
        print("⚠️  outfit_generator.py 尚未实现")
        sys.exit(1)

    tests = [
        ("test_grid_returns_n_paths",       lambda: test_grid_returns_n_paths(generate_outfit_grid)),
        ("test_cropped_files_exist",        lambda: test_cropped_files_exist(generate_outfit_grid)),
        ("test_cropped_size_correct",       lambda: test_cropped_size_correct(generate_outfit_grid)),
        ("test_regenerate_returns_single",  lambda: test_regenerate_returns_single_path(regenerate_single_outfit)),
        ("test_image2_unavailable_raises",  lambda: test_image2_unavailable_raises(generate_outfit_grid)),
    ]

    print("\n=== test_outfit_generator ===")
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
