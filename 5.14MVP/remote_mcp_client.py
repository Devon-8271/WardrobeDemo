"""
Remote MCP client — 调用 ootd-images-backend 的 MCP JSON-RPC 接口。

远端 MCP Server 地址: http://192.168.31.113:9001/mcp
协议: JSON-RPC 2.0 over HTTP POST (streamable-http, SSE 响应)
"""

from __future__ import annotations

import json as _json
import threading
import httpx

REMOTE_MCP_URL = "http://192.168.31.113:9001/mcp"
_JSONRPC_ID = 0
_LOCK = threading.Lock()

_SESSION: httpx.Client | None = None
_SESSION_ID: str | None = None


def _get_session() -> httpx.Client:
    """懒初始化 httpx session，发送 MCP initialize 获取 session ID。"""
    global _SESSION, _SESSION_ID
    if _SESSION is not None:
        return _SESSION
    _SESSION = httpx.Client(
        timeout=httpx.Timeout(120, connect=5),
        trust_env=False,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        resp = _SESSION.post(
            REMOTE_MCP_URL,
            json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "wardrobe-demo", "version": "1.0"},
                },
            },
        )
        sid = resp.headers.get("mcp-session-id")
        if sid:
            _SESSION_ID = sid
            _SESSION.headers["mcp-session-id"] = sid
    except Exception:
        pass
    return _SESSION


def _parse_sse_json(text: str) -> dict:
    """从 SSE 响应中提取 data 行的 JSON。"""
    for line in text.split("\n"):
        if line.startswith("data: "):
            return _json.loads(line[6:])
    # 回退：直接尝试解析（非 SSE 响应）
    return _json.loads(text)


def _call_tool(name: str, arguments: dict) -> dict:
    """底层 JSON-RPC 调用，返回 result content 中的 parsed JSON。"""
    global _JSONRPC_ID, _SESSION_ID
    with _LOCK:
        _JSONRPC_ID += 1
        rid = _JSONRPC_ID

    payload = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }

    session = _get_session()
    resp = session.post(REMOTE_MCP_URL, json=payload)
    resp.raise_for_status()

    # 捕获 session ID（initialize 可能未返回但 tools/call 返回了）
    sid = resp.headers.get("mcp-session-id")
    if sid and not _SESSION_ID:
        _SESSION_ID = sid
        session.headers["mcp-session-id"] = sid

    body = _parse_sse_json(resp.text)
    if "error" in body:
        raise RuntimeError(body["error"].get("message", str(body["error"])))

    content = body.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            return _json.loads(item["text"])
    return {}


# ── 公开工具 ────────────────────────────────────────────────────────────────────


def submit_outfit_photo(user_id: str, *, image_url: str = "", image_base64: str = "") -> dict:
    """上传穿搭原图，VLM 检测图中所有衣物。返回 {ok, raw_id, items}。"""
    args: dict = {"user_id": user_id}
    if image_base64:
        args["image_base64"] = image_base64
    else:
        args["image_url"] = image_url
    return _call_tool("submit_outfit_photo", args)


def generate_item_image(*, image_url: str = "", image_base64: str = "", item_description: str | None = None) -> dict:
    """对衣物原图生成白底商品图（image2）。返回 {ok, image_url}。"""
    args: dict = {}
    if image_base64:
        args["image_base64"] = image_base64
    else:
        args["image_url"] = image_url
    if item_description:
        args["item_description"] = item_description
    return _call_tool("generate_item_image", args)


def extract_item_tags(*, image_url: str = "", image_base64: str = "") -> dict:
    """对白底单品图提取结构化元数据。返回 {ok, fields: {...}}。"""
    args: dict = {}
    if image_base64:
        args["image_base64"] = image_base64
    else:
        args["image_url"] = image_url
    return _call_tool("extract_item_tags", args)


def health() -> bool:
    """测试远端 MCP 是否可达。"""
    try:
        _get_session()
        resp = _SESSION.post(
            REMOTE_MCP_URL,
            json={"jsonrpc": "2.0", "id": 9999, "method": "tools/list", "params": {}},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False
