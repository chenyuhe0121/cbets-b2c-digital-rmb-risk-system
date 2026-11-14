"""合约状态机：状态流转是唯一入口，任何资金动作都必须先过这里。

用 status 做乐观并发控制（UPDATE ... WHERE status=当前状态），并发进来只有一个能改，
另一个会拿到 409，避免同一订单被释放两次。
"""
from __future__ import annotations

import sqlite3

from ..db import now_iso, iso_plus_hours
from ..errors import conflict, not_found

STATUS_LOCKED = "locked"
STATUS_SHIPPED = "shipped"
STATUS_RECEIVED = "received"
STATUS_AFTER_SALES = "after_sales"
STATUS_ARBITRATION = "arbitration"
STATUS_RELEASED = "released"
STATUS_REFUNDED = "refunded"
STATUS_PARTIAL_REFUNDED = "partial_refunded"
STATUS_CLOSED = "closed"

TERMINAL = {STATUS_RELEASED, STATUS_REFUNDED, STATUS_PARTIAL_REFUNDED, STATUS_CLOSED}

TRANSITIONS: dict[str, dict[str, str]] = {
    STATUS_LOCKED: {"ship": STATUS_SHIPPED, "release": STATUS_RELEASED,
                    "open_dispute": STATUS_AFTER_SALES},
    STATUS_SHIPPED: {"receive": STATUS_RECEIVED, "release": STATUS_RELEASED,
                     "open_dispute": STATUS_AFTER_SALES},
    STATUS_RECEIVED: {"open_dispute": STATUS_AFTER_SALES, "release": STATUS_RELEASED},
    STATUS_AFTER_SALES: {"arbitrate": STATUS_ARBITRATION, "refund": STATUS_REFUNDED,
                         "partial_refund": STATUS_PARTIAL_REFUNDED, "release": STATUS_RELEASED},
    STATUS_ARBITRATION: {"refund": STATUS_REFUNDED, "partial_refund": STATUS_PARTIAL_REFUNDED,
                         "release": STATUS_RELEASED},
}

ORDER_STATUS = {
    STATUS_LOCKED: "paid_escrowed", STATUS_SHIPPED: "shipped", STATUS_RECEIVED: "received",
    STATUS_AFTER_SALES: "after_sales", STATUS_ARBITRATION: "arbitrating",
    STATUS_RELEASED: "completed", STATUS_REFUNDED: "refunded",
    STATUS_PARTIAL_REFUNDED: "partially_refunded",
}


def create_contract(conn: sqlite3.Connection, *, order_id: str, seller_id: str, amount_cents: int,
                    hold_hours: int | None = None) -> dict:
    existing = conn.execute("SELECT * FROM contracts WHERE order_id=?", (order_id,)).fetchone()
    if existing:
        return dict(existing)
    ts = now_iso()
    from ..config import settings
    hold_until = iso_plus_hours(hold_hours if hold_hours is not None else settings.escrow_hold_hours)
    conn.execute(
        "INSERT INTO contracts(order_id,seller_id,locked_cents,released_cents,refunded_cents,status,"
        "hold_until,version,updated_at) VALUES(?,?,?,0,0,?,?,0,?)",
        (order_id, seller_id, amount_cents, STATUS_LOCKED, hold_until, ts),
    )
    conn.execute("UPDATE orders SET status=?, hold_until=?, paid_at=? WHERE order_id=?",
                 (ORDER_STATUS[STATUS_LOCKED], hold_until, ts, order_id))
    return dict(conn.execute("SELECT * FROM contracts WHERE order_id=?", (order_id,)).fetchone())


def get_contract(conn: sqlite3.Connection, order_id: str) -> dict:
    row = conn.execute("SELECT * FROM contracts WHERE order_id=?", (order_id,)).fetchone()
    if not row:
        raise not_found(f"订单 {order_id} 的担保合约")
    return dict(row)


def transition(conn: sqlite3.Connection, *, order_id: str, event: str) -> dict:
    contract = get_contract(conn, order_id)
    current = contract["status"]
    allowed = TRANSITIONS.get(current, {})
    if event not in allowed:
        raise conflict("ILLEGAL_TRANSITION",
                       f"合约当前状态 {current} 不允许执行 {event}",
                       {"current_status": current, "allowed_events": sorted(allowed)})
    target = allowed[event]
    ts = now_iso()
    updated = conn.execute(
        "UPDATE contracts SET status=?, version=version+1, updated_at=? "
        "WHERE order_id=? AND status=? AND version=?",
        (target, ts, order_id, current, contract["version"]),
    ).rowcount
    if not updated:
        raise conflict("CONCURRENT_STATE_CHANGE", "合约状态已被并发修改，请重试")

    order_status = ORDER_STATUS[target]
    sets = ["status=?"]
    args: list = [order_status]
    if target == STATUS_SHIPPED:
        sets.append("shipped_at=?")
        args.append(ts)
    if target == STATUS_RECEIVED:
        sets.append("received_at=?")
        args.append(ts)
    if target in TERMINAL:
        sets.append("closed_at=?")
        args.append(ts)
    args.append(order_id)
    conn.execute(f"UPDATE orders SET {','.join(sets)} WHERE order_id=?", args)
    return {"order_id": order_id, "from": current, "to": target, "event": event}
