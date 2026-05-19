import sqlite3
import json

DB_PATH = "wardrobe.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS wardrobe (
            item_id        TEXT PRIMARY KEY,
            category       TEXT,
            type           TEXT NOT NULL,
            raw_type       TEXT,
            color          TEXT NOT NULL,
            style          TEXT NOT NULL,
            season         TEXT NOT NULL,
            warmth         TEXT,
            fit            TEXT,
            material       TEXT DEFAULT '[]',
            description    TEXT,
            image_url      TEXT,
            image_crop_url TEXT,
            source         TEXT DEFAULT 'real',
            upload_time    TEXT NOT NULL
        )
    """)

    # 兼容旧数据库：补充缺失列
    for col, definition in [
        ("source",         "TEXT DEFAULT 'real'"),
        ("fit",            "TEXT"),
        ("category",       "TEXT"),
        ("raw_type",       "TEXT"),
        ("warmth",         "TEXT"),
        ("material",       "TEXT DEFAULT '[]'"),
        ("image_crop_url", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE wardrobe ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id          TEXT PRIMARY KEY,
            photo_url        TEXT,
            height           TEXT,
            body_type        TEXT,
            skin_tone        TEXT,
            style_preference TEXT,
            temp_offset      INTEGER DEFAULT 0,
            personal_color   TEXT,
            style_tags       TEXT DEFAULT '[]',
            upload_time      TEXT NOT NULL
        )
    """)

    # 兼容旧数据库：补充缺失列
    for col, definition in [
        ("temp_offset",     "INTEGER DEFAULT 0"),
        ("personal_color",  "TEXT"),
        ("last_outfit_date","TEXT DEFAULT ''"),
        ("style_tags",      "TEXT DEFAULT '[]'"),
    ]:
        try:
            c.execute(f"ALTER TABLE user_profile ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS look (
            look_id   TEXT PRIMARY KEY,
            date      TEXT NOT NULL,
            item_ids  TEXT NOT NULL,
            photo_url TEXT,
            scene     TEXT DEFAULT '',
            source    TEXT NOT NULL,
            user_id   TEXT NOT NULL DEFAULT 'default',
            tryon_url TEXT DEFAULT ''
        )
    """)

    # 兼容旧数据库：补充缺失列
    try:
        c.execute("ALTER TABLE look ADD COLUMN tryon_url TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


def insert_wardrobe_item(item: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO wardrobe
            (item_id, category, type, raw_type, color, style, season, warmth, fit, material, description, image_url, image_crop_url, source, upload_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item["item_id"],
        item.get("category", ""),
        item["type"],
        item.get("raw_type", item["type"]),
        json.dumps(item["color"], ensure_ascii=False),
        json.dumps(item["style"], ensure_ascii=False),
        json.dumps(item["season"], ensure_ascii=False),
        item.get("warmth", ""),
        item.get("fit", ""),
        json.dumps(item.get("material", []), ensure_ascii=False),
        item.get("description", ""),
        item.get("image_url", ""),
        item.get("image_crop_url", ""),
        item.get("source", "real"),
        item["upload_time"],
    ))
    conn.commit()
    conn.close()


_WARDROBE_COLS = ["item_id", "category", "type", "raw_type", "color", "style", "season", "warmth", "fit", "material", "description", "image_url", "image_crop_url", "source", "upload_time"]
_WARDROBE_SELECT = "SELECT item_id, category, type, raw_type, color, style, season, warmth, fit, material, description, image_url, image_crop_url, source, upload_time FROM wardrobe"


def _parse_wardrobe_row(row) -> dict:
    item = dict(zip(_WARDROBE_COLS, row))
    item["color"] = json.loads(item["color"])
    item["style"] = json.loads(item["style"])
    item["season"] = json.loads(item["season"])
    item["material"] = json.loads(item["material"] or "[]")
    return item


def get_all_wardrobe_items(source_filter=None):
    conn = get_conn()
    c = conn.cursor()
    if source_filter:
        c.execute(f"{_WARDROBE_SELECT} WHERE source = ? ORDER BY upload_time DESC", (source_filter,))
    else:
        c.execute(f"{_WARDROBE_SELECT} ORDER BY upload_time DESC")
    rows = c.fetchall()
    conn.close()
    return [_parse_wardrobe_row(r) for r in rows]


def get_wardrobe_item(item_id: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"{_WARDROBE_SELECT} WHERE item_id = ?", (item_id,))
    row = c.fetchone()
    conn.close()
    return _parse_wardrobe_row(row) if row else None


def delete_wardrobe_item(item_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM wardrobe WHERE item_id = ?", (item_id,))
    conn.commit()
    conn.close()


def upsert_user_profile(profile: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO user_profile (user_id, photo_url, height, body_type, skin_tone, style_preference, temp_offset, personal_color, upload_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            photo_url        = excluded.photo_url,
            height           = excluded.height,
            body_type        = excluded.body_type,
            skin_tone        = excluded.skin_tone,
            style_preference = excluded.style_preference,
            personal_color   = excluded.personal_color,
            upload_time      = excluded.upload_time
    """, (
        profile["user_id"],
        profile.get("photo_url", ""),
        profile.get("height", ""),
        profile.get("body_type", ""),
        profile.get("skin_tone", ""),
        json.dumps(profile.get("style_preference", []), ensure_ascii=False),
        profile.get("temp_offset", 0),
        profile.get("personal_color", ""),
        profile["upload_time"],
    ))
    conn.commit()
    conn.close()


def update_user_photo(user_id: str, photo_url: str, upload_time: str):
    """窄更新：只动 photo_url + upload_time，保留其他字段（height / body_type / 等）。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM user_profile WHERE user_id = ?", (user_id,))
    if c.fetchone():
        c.execute(
            "UPDATE user_profile SET photo_url = ?, upload_time = ? WHERE user_id = ?",
            (photo_url, upload_time, user_id),
        )
    else:
        c.execute(
            """INSERT INTO user_profile (user_id, photo_url, height, body_type, skin_tone,
                                          style_preference, temp_offset, personal_color, upload_time)
               VALUES (?, ?, '', '', '', '[]', 0, '', ?)""",
            (user_id, photo_url, upload_time),
        )
    conn.commit()
    conn.close()


def update_last_outfit_date(user_id: str = "default", date_str: str = ""):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE user_profile SET last_outfit_date = ? WHERE user_id = ?", (date_str, user_id))
    conn.commit()
    conn.close()


def update_temp_offset(delta: int, user_id: str = "default"):
    """根据用户冷热反馈调整温感偏移，限制在 [-10, 10]"""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE user_profile SET temp_offset = MAX(-10, MIN(10, temp_offset + ?)) WHERE user_id = ?",
        (delta, user_id),
    )
    conn.commit()
    # 返回更新后的值
    c.execute("SELECT temp_offset FROM user_profile WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def get_user_profile(user_id: str = "default"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT user_id, photo_url, height, body_type, skin_tone, style_preference,
               temp_offset, personal_color, last_outfit_date, style_tags, upload_time
        FROM user_profile WHERE user_id = ?
    """, (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["user_id", "photo_url", "height", "body_type", "skin_tone", "style_preference",
            "temp_offset", "personal_color", "last_outfit_date", "style_tags", "upload_time"]
    profile = dict(zip(cols, row))
    profile["style_preference"] = json.loads(profile["style_preference"])
    profile["style_tags"] = json.loads(profile["style_tags"])
    return profile


def update_style_tags(user_id: str, tags: list):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE user_profile SET style_tags = ? WHERE user_id = ?",
              (json.dumps(tags, ensure_ascii=False), user_id))
    conn.commit()
    conn.close()


# ── Look CRUD ─────────────────────────────────────────────────────────────────

def insert_look(look: dict) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO look (look_id, date, item_ids, photo_url, scene, source, user_id, tryon_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        look["look_id"],
        look["date"],
        json.dumps(look["item_ids"], ensure_ascii=False),
        look.get("photo_url", ""),
        look.get("scene", ""),
        look["source"],
        look.get("user_id", "default"),
        look.get("tryon_url", ""),
    ))
    conn.commit()
    conn.close()
    return look["look_id"]


def update_look_tryon_url(look_id: str, tryon_url: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE look SET tryon_url = ? WHERE look_id = ?", (tryon_url, look_id))
    conn.commit()
    conn.close()


def delete_look(look_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM look WHERE look_id = ?", (look_id,))
    conn.commit()
    conn.close()


def has_look_on_date(date_str: str, user_id: str = "default") -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM look WHERE user_id = ? AND date = ? LIMIT 1",
        (user_id, date_str),
    )
    found = c.fetchone() is not None
    conn.close()
    return found


def get_look(look_id: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT look_id, date, item_ids, photo_url, scene, source, tryon_url FROM look WHERE look_id = ?",
        (look_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["look_id", "date", "item_ids", "photo_url", "scene", "source", "tryon_url"]
    look = dict(zip(cols, row))
    look["item_ids"] = json.loads(look["item_ids"])
    return look


def get_looks(user_id: str = "default", scene: str = None, limit: int = 30) -> list:
    conn = get_conn()
    c = conn.cursor()
    if scene:
        c.execute(
            "SELECT look_id, date, item_ids, photo_url, scene, source, tryon_url FROM look "
            "WHERE user_id = ? AND scene = ? ORDER BY date DESC LIMIT ?",
            (user_id, scene, limit),
        )
    else:
        c.execute(
            "SELECT look_id, date, item_ids, photo_url, scene, source, tryon_url FROM look "
            "WHERE user_id = ? ORDER BY date DESC LIMIT ?",
            (user_id, limit),
        )
    rows = c.fetchall()
    conn.close()
    cols = ["look_id", "date", "item_ids", "photo_url", "scene", "source", "tryon_url"]
    result = []
    for row in rows:
        look = dict(zip(cols, row))
        look["item_ids"] = json.loads(look["item_ids"])
        result.append(look)
    return result
