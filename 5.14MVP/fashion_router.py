"""
fashion_router.py
Fashion Agent 内部意图路由。

Route keys:
  recommend      → 今天穿什么 / 天气+场合推荐
  swap_item      → 换某件单品（内联换装）
  quick_tryon    → 随手试穿外部图片
  wardrobe_query → 查看/检索衣橱
  save_look      → 保存当前搭配 / 记录 OOTD
  unknown        → 兜底，降级为 recommend
"""

import os
from dotenv import load_dotenv
load_dotenv()

# ── 品类关键词（swap_item 时提取目标品类） ─────────────────────────────────────

_CATEGORY_KEYWORDS = {
    "上装": ["上衣", "T恤", "衬衫", "针织衫", "卫衣", "背心", "吊带", "马甲"],
    "下装": ["裤子", "牛仔裤", "阔腿裤", "长裤", "短裤", "半身裙", "裙子"],
    "外套": ["外套", "夹克", "风衣", "大衣", "棉服", "羽绒服", "西装"],
    "鞋履": ["鞋", "靴", "运动鞋", "高跟鞋", "乐福鞋", "凉鞋"],
    "配件": ["包", "帽子", "围巾", "腰带", "首饰"],
}

def _extract_category(text: str):
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return cat
    return None


# ── 规则分类器（主分类器 / LLM 失败时兜底） ───────────────────────────────────

_RULES = {
    "recommend": [
        "穿什么", "搭配", "推荐", "今天穿", "明天穿", "适合穿",
        "怎么穿", "搭什么", "穿搭建议", "天气", "场合",
    ],
    "swap_item": [
        "换", "替换", "改成", "换成",
    ],
    "quick_tryon": [
        "试穿", "试一下", "试试", "穿上看看", "上身效果", "穿在我身上",
    ],
    "wardrobe_query": [
        "衣橱", "有几件", "有什么", "查一下", "列表", "我的衣服",
    ],
    "save_look": [
        "保存", "记录", "存下来", "OOTD", "今天穿了", "穿搭日志",
    ],
}

def _rule_classify(text: str) -> str:
    # swap_item 优先级最高（"换"字明确）
    if any(kw in text for kw in _RULES["swap_item"]):
        # 排除"换个推荐"这类误触
        if not any(kw in text for kw in ["推荐", "建议", "方案"]):
            return "swap_item"
    for key in ["save_look", "wardrobe_query", "quick_tryon", "recommend"]:
        if any(kw in text for kw in _RULES[key]):
            return key
    return "unknown"


# ── LLM 分类器（Groq） ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是智能衣橱 Fashion Agent 的意图分类器。
用户输入一句话，判断意图，只返回以下其中一个词，不要任何其他内容：

recommend      - 想要穿搭推荐，问今天/明天穿什么，或提到天气、场合
swap_item      - 想换某一件单品（换条裤子、换双鞋、上衣换成白色）
quick_tryon    - 想试穿一件外部图片里的衣服（随手发图试穿）
wardrobe_query - 想查看或检索自己衣橱里的单品
save_look      - 想保存当前搭配或记录今日穿搭
unknown        - 无法判断

关键区分：含「换」字且指向具体品类 → swap_item；泛泛问穿什么 → recommend。"""

def _llm_classify(text: str) -> str:
    try:
        from groq import Groq
        groq  = Groq(api_key=os.getenv("GROQ_API_KEY"))
        resp  = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": text},
            ],
            max_tokens=10,
            temperature=0,
        )
        result = resp.choices[0].message.content.strip().lower()
        valid  = {"recommend", "swap_item", "quick_tryon", "wardrobe_query", "save_look", "unknown"}
        return result if result in valid else "unknown"
    except Exception as e:
        print(f"  [fashion_router] LLM 调用失败，降级到规则: {e}")
        return _rule_classify(text)


# ── 公开接口 ───────────────────────────────────────────────────────────────────

def route(user_input: str) -> dict:
    """
    主入口。返回路由结果：
    {
      "key":      "recommend" | "swap_item" | "quick_tryon" | "wardrobe_query" | "save_look" | "unknown",
      "input":    原始输入,
      "category": 品类（仅 swap_item 时填充，其余为 None）,
    }
    """
    key      = _llm_classify(user_input)
    category = _extract_category(user_input) if key == "swap_item" else None

    # unknown 降级为 recommend（用户在 Fashion Agent 里说话，大概率是想穿搭）
    if key == "unknown":
        key = "unknown"   # 保留 unknown，让调用方决定如何展示

    return {
        "key":      key,
        "input":    user_input,
        "category": category,
    }
