"""Mock 物流适配器：用商户历史 logistics_offset_hours 模拟揽收时间。

真实替换点：菜鸟 / 燕文 / 4PX 的运单创建与轨迹查询 OpenAPI。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import sqlite3

from ..db import connect, now_iso, tx
from ..errors import bad_request, not_found
from .base import LogisticsAdapter


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class MockLogisticsAdapter(LogisticsAdapter):
    def create_shipment(self, *, order_id: str, carrier: str, tracking_no: str,
                        order_created_at: str) -> dict:
        offset_hours = 6.0
        conn = connect()
        try:
            seller = conn.execute(
                "SELECT p.profile FROM orders o JOIN parties p ON p.party_id=o.seller_id "
                "WHERE o.order_id=?", (order_id,)
            ).fetchone()
            if seller:
                offset_hours = float(json.loads(seller["profile"]).get("logistics_offset_hours", 6))
            pickup = _parse_iso(order_created_at) + timedelta(hours=offset_hours)
            events = [
                {"time": pickup.isoformat(timespec="seconds"), "status": "PICKED_UP", "location": "深圳仓"},
                {"time": (pickup + timedelta(hours=18)).isoformat(timespec="seconds"),
                 "status": "IN_TRANSIT", "location": "香港中转"},
            ]
            with tx(conn) as c:
                c.execute(
                    "INSERT OR REPLACE INTO shipments(order_id,tracking_no,carrier,pickup_time,status,"
                    "events,updated_at) VALUES(?,?,?,?,'in_transit',?,?)",
                    (order_id, tracking_no, carrier, pickup.isoformat(timespec="seconds"),
                     json.dumps(events, ensure_ascii=False), now_iso()),
                )
                c.execute(
                    "INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                    ("logistics.shipment_created", order_id,
                     json.dumps({"tracking_no": tracking_no, "carrier": carrier}, ensure_ascii=False),
                     now_iso()),
                )
            return {"tracking_no": tracking_no, "carrier": carrier,
                    "pickup_time": pickup.isoformat(timespec="seconds"), "events": events}
        finally:
            conn.close()

    def query_tracking(self, tracking_no: str) -> dict:
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM shipments WHERE tracking_no=?", (tracking_no,)).fetchone()
            if not row:
                raise not_found(f"运单 {tracking_no}")
            return {"tracking_no": row["tracking_no"], "status": row["status"],
                    "carrier": row["carrier"], "pickup_time": row["pickup_time"],
                    "events": json.loads(row["events"])}
        finally:
            conn.close()

    def mark_delivered(self, tracking_no: str, signed_by: str = "buyer") -> dict:
        """模拟签收，售后场景需要用到。"""
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM shipments WHERE tracking_no=?", (tracking_no,)).fetchone()
            if not row:
                raise bad_request(f"运单 {tracking_no} 不存在，无法签收")
            events = json.loads(row["events"])
            events.append({"time": now_iso(), "status": "DELIVERED", "location": signed_by})
            with tx(conn) as c:
                c.execute("UPDATE shipments SET status='delivered', events=?, updated_at=? "
                          "WHERE tracking_no=?", (json.dumps(events, ensure_ascii=False), now_iso(),
                                                  tracking_no))
            return {"tracking_no": tracking_no, "status": "delivered"}
        finally:
            conn.close()
