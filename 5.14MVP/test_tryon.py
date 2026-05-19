"""
快速测试试穿 A+B=C，不需要 DB 和衣物数据。

用法：
  python test_tryon.py <全身照> <衣物图> [衣物描述]

示例：
  python test_tryon.py me.jpg shirt.jpg
  python test_tryon.py me.jpg shirt.jpg "白色宽松棉T恤"
"""

import sys
import os
from phase2_tryon import _build_tryon_prompt, _call_image2_tryon, FIT_OPTIONS


def run(user_photo: str, item_image: str, description: str = ""):
    for path, label in [(user_photo, "全身照"), (item_image, "衣物图")]:
        if not os.path.isfile(path):
            print(f"❌ {label}不存在: {path}")
            sys.exit(1)

    item = {
        "description": description or os.path.basename(item_image),
        "type":        "上衣",
        "color":       [""],
        "style":       [],
        "image_url":   item_image,
    }

    fit_label, fit_desc = FIT_OPTIONS[1]  # 常规
    prompt = _build_tryon_prompt(item, fit_label, fit_desc)

    print(f"\nB (全身照): {user_photo}")
    print(f"A (衣物图): {item_image}")
    print(f"\n--- Prompt ---\n{prompt}\n--------------\n")

    confirm = input("提交生成？(y/n): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    result = _call_image2_tryon(user_photo, item_image, prompt)
    print(f"\n✅ 生成完毕: {result}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法：python test_tryon.py <全身照> <衣物图> [衣物描述]")
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
