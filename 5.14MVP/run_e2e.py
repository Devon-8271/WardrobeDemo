"""
端到端联调脚本：真实衣橱数据 → 推荐 → 生图 → 裁切

用法：
  python run_e2e.py                        # 用 DB 真实数据 + 自动定位天气
  python run_e2e.py --temp 12 --desc 小雨  # 指定天气
  python run_e2e.py --n 2                  # 只生成 2 套（省时间）
  python run_e2e.py --dry-run              # 只跑推荐，不调 image2
"""

import argparse
import os
import sys

import db
import image2_client
from outfit_recommender import recommend_outfits
from outfit_generator   import generate_outfit_grid


# ── 天气获取（复用 demo 逻辑） ─────────────────────────────────────────────────

def _get_weather(temp_c=None, desc=None):
    if temp_c is not None:
        return {"temp_c": temp_c, "description": desc or ""}
    try:
        import requests
        r = requests.get("https://wttr.in/?format=j1", timeout=5)
        data = r.json()
        current = data["current_condition"][0]
        return {
            "temp_c":      float(current["temp_C"]),
            "description": current["weatherDesc"][0]["value"],
        }
    except Exception as e:
        print(f"  ⚠️  天气获取失败（{e}），使用默认 15°C")
        return {"temp_c": 15, "description": "晴"}


# ── 主流程 ─────────────────────────────────────────────────────────────────────

def run(temp_c=None, desc=None, n=4, dry_run=False, user_photo=None):
    db.init_db()

    # 1. 衣橱状态
    items = db.get_all_wardrobe_items(source_filter="real")
    if not items:
        print("❌ 衣橱为空，请先用 import_batch.py 导入单品")
        sys.exit(1)
    print(f"\n衣橱共 {len(items)} 件单品")

    # 2. 天气
    weather = _get_weather(temp_c, desc)
    print(f"天气：{weather['temp_c']}°C  {weather['description']}")

    # 3. 推荐
    print(f"\n[Step 1] 推荐 {n} 套穿搭...")
    outfits = recommend_outfits(user_id="default", weather=weather, n=n)
    if not outfits:
        print("❌ 推荐结果为空，衣橱单品可能不足（需要上装+下装各至少一件）")
        sys.exit(1)

    for i, o in enumerate(outfits, 1):
        item_types = []
        for iid in o["item_ids"]:
            it = db.get_wardrobe_item(iid)
            if it:
                item_types.append(f"{it['type']}({it['color'][0] if it['color'] else ''})")
        print(f"  套 {i}：{' + '.join(item_types)}  [{', '.join(o['style_tags'])}]")
        print(f"         {o['caption']}")

    if dry_run:
        print("\n[dry-run] 跳过 image2 生成")
        return

    # 4. image2 健康检查
    print("\n[Step 2] 检查 image2 服务...")
    if not image2_client.healthz():
        print("❌ image2 服务不可达，请检查 Wi-Fi 或关闭 VPN")
        sys.exit(1)
    print("  ✓ 服务在线")

    # 5. 用户照片
    if not user_photo:
        profile = db.get_user_profile("default")
        user_photo = profile.get("photo_url", "") if profile else ""
    if not user_photo or not os.path.isfile(user_photo):
        print("⚠️  未找到用户全身照，尝试用 user_photos/ 目录下第一张...")
        photos = [f for f in os.listdir("user_photos") if f.lower().endswith((".jpg",".jpeg",".png"))] \
                 if os.path.isdir("user_photos") else []
        if photos:
            user_photo = os.path.join("user_photos", photos[0])
            print(f"  使用：{user_photo}")
        else:
            print("❌ 没有用户照片，无法生成试穿图。请先上传全身照到 user_photos/")
            sys.exit(1)

    # 6. 生图
    print(f"\n[Step 3] 生成 {len(outfits)} 套穿搭拼图（预计 1-5 分钟）...")
    paths = generate_outfit_grid(user_photo=user_photo, outfits=outfits)

    # 7. 结果
    print(f"\n{'='*50}")
    print(f"✅ 生成完成，共 {len(paths)} 张")
    for i, (p, o) in enumerate(zip(paths, outfits), 1):
        print(f"  套 {i}：{p}  [{o['caption']}]")
    print()


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="端到端穿搭推荐+生图联调")
    parser.add_argument("--temp",    type=float, help="气温（°C），不填则自动获取")
    parser.add_argument("--desc",    type=str,   help="天气描述，如「小雨」")
    parser.add_argument("--n",       type=int,   default=4, help="生成套数，默认 4")
    parser.add_argument("--photo",   type=str,   help="用户全身照路径")
    parser.add_argument("--dry-run", action="store_true", help="只跑推荐，不调 image2")
    args = parser.parse_args()

    run(
        temp_c    = args.temp,
        desc      = args.desc,
        n         = args.n,
        dry_run   = args.dry_run,
        user_photo= args.photo,
    )
