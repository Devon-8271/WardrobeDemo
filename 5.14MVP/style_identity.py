"""
style_identity.py
从 look 历史推断用户风格档案，写回 user_profile.style_tags。
"""

from collections import Counter
from datetime import date

import db


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
            if not it:
                continue
            target = this_styles if look["date"] >= this_month else last_styles
            target.extend(it.get("style", []))

    this_cnt = Counter(this_styles)
    last_cnt = Counter(last_styles)

    total_this = sum(this_cnt.values()) or 1
    total_last = sum(last_cnt.values()) or 1

    distribution = {k: round(v / total_this, 3) for k, v in this_cnt.most_common()}
    tags = [k for k, _ in this_cnt.most_common(5)]
    trend = {
        tag: round(
            this_cnt.get(tag, 0) / total_this - last_cnt.get(tag, 0) / total_last, 3
        )
        for tag in set(list(this_cnt) + list(last_cnt))
    }

    return {"tags": tags, "distribution": distribution, "trend": trend}
