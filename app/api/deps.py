"""API 依赖：数据库连接与管理员鉴权。"""
from __future__ import annotations

from fastapi import Header, HTTPException

from ..config import settings
from ..db import connect


def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def require_admin(x_admin_key: str = Header(default="", alias="X-Admin-Key")) -> None:
    """未配置 CBETS_ADMIN_KEY 时处于开发模式，任何人都能调；公网部署务必配置。"""
    if settings.admin_key and x_admin_key != settings.admin_key:
        raise HTTPException(status_code=401, detail="管理员密钥无效")
