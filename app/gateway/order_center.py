"""订单中心：下单、查询聚合视图。"""
from __future__ import annotations

import json
import uuid

import sqlite3

from ..db import connect, now_iso, tx
from ..errors import bad_request, not_found


def _order_id() -> str:
    return "ORD" + uuid.uuid4().hex[:14].upper()


def create_order(conn: sqlite3.Connection, *, buyer_id: str, seller_id: str, amount_cents: int,
                 sku_desc: str, currency: str = "CNY", order_id: str | None = None) -> dict:
    if amount_cents <= 0:
        raise bad_request("订单金额必须大于 0 分")
    for party_id, role in ((buyer_id, "buyer"), (seller_id, "seller")):
        row = conn.execute("SELECT role FROM parties WHERE party_id=?", (party_id,)).fetchone()
        if not row:
            raise not_found(f"{role} {party_id}")
        if row["role"] != role:
            raise bad_request(f"{party_id} 的角色不是 {role}")
    oid = order_id or _order_id()
    if conn.execute("SELECT 1 FROM orders WHERE order_id=?", (oid,)).fetchone():
        raise bad_request(f"订单号 {oid} 已存在")
    with tx(conn) as c:
        c.execute(
            "INSERT INTO orders(order_id,buyer_id,seller_id,amount_cents,currency,sku_desc,status,"
            "review_flag,created_at) VALUES(?,?,?,?,?,?,'created',0,?)",
            (oid, buyer_id, seller_id, amount_cents, currency, sku_desc, now_iso()),
        )
        c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                  ("order.created", oid,
                   json.dumps({"amount_cents": amount_cents, "buyer_id": buyer_id,
                               "seller_id": seller_id}, ensure_ascii=False), now_iso()))
    return order_detail(conn, oid)


def get_order(conn: sqlite3.Connection, order_id: str) -> dict:
    row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not row:
        raise not_found(f"订单 {order_id}")
    return dict(row)


def order_detail(conn: sqlite3.Connection, order_id: str) -> dict:
    order = get_order(conn, order_id)
    contract = conn.execute("SELECT * FROM contracts WHERE order_id=?", (order_id,)).fetchone()
    risk = conn.execute("SELECT buyer_prob,seller_prob,final_score,final_level,decision,reasons,"
                        "hit_rules,model_version,created_at FROM risk_records WHERE order_id=? "
                        "ORDER BY id DESC LIMIT 1", (order_id,)).fetchone()
    entries = conn.execute("SELECT entry_type,amount_cents,locked_after,settled_after,ref_no,memo,"
                           "entry_hash,created_at FROM ledger_entries WHERE order_id=? ORDER BY seq",
                           (order_id,)).fetchall()
    shipment = conn.execute("SELECT tracking_no,carrier,pickup_time,status FROM shipments "
                            "WHERE order_id=?", (order_id,)).fetchone()
    disputes = conn.execute("SELECT dispute_id,dtype,status,refund_cents,created_at FROM disputes "
                            "WHERE order_id=? ORDER BY created_at", (order_id,)).fetchall()
    trade = conn.execute("SELECT trade_no,status,amount_cents FROM cbets_trades WHERE order_id=?",
                         (order_id,)).fetchone()
    return {
        "order": order,
        "contract": dict(contract) if contract else None,
        "risk": {
            **{k: risk[k] for k in risk.keys() if k != "reasons"},
            "reasons": json.loads(risk["reasons"]),
            "hit_rules": json.loads(risk["hit_rules"]),
        } if risk else None,
        "ledger": [dict(e) for e in entries],
        "shipment": dict(shipment) if shipment else None,
        "disputes": [dict(d) for d in disputes],
        "cbets_trade": dict(trade) if trade else None,
    }


def list_orders(conn: sqlite3.Connection, limit: int = 50, seller_id: str | None = None,
                buyer_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM orders WHERE 1=1"
    args: list = []
    if seller_id:
        sql += " AND seller_id=?"
        args.append(seller_id)
    if buyer_id:
        sql += " AND buyer_id=?"
        args.append(buyer_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        contract = conn.execute("SELECT status,locked_cents,released_cents,refunded_cents "
                                "FROM contracts WHERE order_id=?", (r["order_id"],)).fetchone()
        d["contract_status"] = contract["status"] if contract else None
        out.append(d)
    return out
