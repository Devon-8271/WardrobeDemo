"""
Wardrobe Image2 MCP Server — 包装 ootd-images-backend 的 MCP 工具。

本地 FastMCP 服务，供 Claude Code 或其他 MCP 客户端直接调用。
底层通过 JSON-RPC 转发到 ootd-images-backend 的 MCP server。

启动:
  cd 5.14MVP && .venv/bin/python wardrobe_mcp.py
  # 默认监听 0.0.0.0:9002，streamable-http transport

Claude Code 配置 (~/.claude/mcp.json):
  {
    "wardrobe-image2": {
      "type": "http",
      "url": "http://localhost:9002/mcp"
    }
  }
"""

from __future__ import annotations

import os
from mcp.server.fastmcp import FastMCP

import remote_mcp_client as remote

HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "9002"))

mcp = FastMCP("wardrobe-image2", host=HOST, port=PORT)


# ── 工具 ──────────────────────────────────────────────────────────────────────────


@mcp.tool()
def submit_outfit_photo(user_id: str, image_url: str) -> dict:
    """上传一张穿搭原图，VLM 识别图中所有衣物单品。

    Args:
        user_id: 用户标识，任意字符串
        image_url: 图片 HTTP 地址

    Returns:
        {ok, raw_id, items: [{item_index, category, type, raw_type, color, ...}]}
    """
    return remote.submit_outfit_photo(user_id, image_url)


@mcp.tool()
def generate_item_image(image_url: str, item_description: str | None = None) -> dict:
    """对衣物原图调 image2 生成白底商品图。

    Args:
        image_url: 衣物原图 HTTP 地址
        item_description: 衣物描述（如"白色圆领T恤"），帮助生图更精准

    Returns:
        {ok, image_url: "http://..."}
    """
    return remote.generate_item_image(image_url, item_description)


@mcp.tool()
def extract_item_tags(image_url: str) -> dict:
    """对白底单品图提取结构化元数据（category / color / style / season 等）。

    Args:
        image_url: 白底单品图 HTTP 地址

    Returns:
        {ok, fields: {category, type, color, style, season, warmth, fit, description}}
    """
    return remote.extract_item_tags(image_url)


@mcp.tool()
def health() -> dict:
    """检查远端 ootd-images-backend MCP 是否可达。"""
    ok = remote.health()
    return {"ok": ok, "remote": remote.REMOTE_MCP_URL}


# ── Entry ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[wardrobe-mcp] starting on {HOST}:{PORT}")
    mcp.run(transport="streamable-http")
