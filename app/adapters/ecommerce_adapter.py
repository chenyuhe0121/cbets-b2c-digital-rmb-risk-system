"""Mock 电商平台适配器。

真实替换点：TikTok Shop / Shopee / Amazon SP-API 的订单查询与风控回写接口。
"""
from __future__ import annotations

import json
import sqlite3

from ..db import connect, now_iso
from ..errors import not_found
from .base import CommerceAdapter


class MockCommerceAdapter(CommerceAdapter):
    def order_context(self, order_id: str) -> dict:
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                raise not_found(f"订单 {order_id}")
            return {
                "order_id": row["order_id"],
                "buyer_id": row["buyer_id"],
                "seller_id": row["seller_id"],
                "amount_cents": row["amount_cents"],
                "currency": row["currency"],
                "sku": row["sku_desc"],
                "created_at": row["created_at"],
                "platform": "mock-shop",
            }
        finally:
            conn.close()

    def notify_decision(self, *, order_id: str, decision: str, reasons: list[str]) -> dict:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                ("platform.decision_pushed", order_id,
                 json.dumps({"decision": decision, "reasons": reasons}, ensure_ascii=False), now_iso()),
            )
            return {"ok": True, "order_id": order_id, "decision": decision}
        finally:
            conn.close()
