"""支付标记化：用一次性 token 替代钱包号，避免钱包标识在业务库里裸存。

真实环境应接入卡组织/钱包方的 token 服务（TR-31 或等价密钥托管），这里做的是
同一套语义的本地实现：库里只存 sha256(token)。
"""
from __future__ import annotations

import hashlib
import secrets

import sqlite3

from ..db import connect, now_iso, tx
from ..errors import bad_request, not_found


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(conn: sqlite3.Connection, *, buyer_id: str) -> dict:
    row = conn.execute("SELECT wallet_id FROM parties WHERE party_id=? AND role='buyer'",
                       (buyer_id,)).fetchone()
    if not row or not row["wallet_id"]:
        raise not_found(f"买家 {buyer_id} 或其钱包")
    token = "tok_" + secrets.token_urlsafe(24)
    with tx(conn) as c:
        c.execute("INSERT INTO payment_tokens(token_hash,buyer_id,wallet_id,status,created_at)"
                  " VALUES(?,?,?,'active',?)", (_hash(token), buyer_id, row["wallet_id"], now_iso()))
    masked = row["wallet_id"][:4] + "****" + row["wallet_id"][-2:]
    return {"token": token, "buyer_id": buyer_id, "wallet_masked": masked,
            "expires_in": "长期有效（演示实现，生产环境应设 TTL 与单次使用）"}


def resolve_token(conn: sqlite3.Connection, token: str) -> dict:
    row = conn.execute("SELECT * FROM payment_tokens WHERE token_hash=? AND status='active'",
                       (_hash(token),)).fetchone()
    if not row:
        raise bad_request("支付标记无效或已失效")
    return {"buyer_id": row["buyer_id"], "wallet_id": row["wallet_id"]}
