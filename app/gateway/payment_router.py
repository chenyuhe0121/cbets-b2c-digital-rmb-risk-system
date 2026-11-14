"""支付路由：下单后的资金主链路。

顺序很重要：
1. 事中风控评分（block 直接拒绝，review 放行但延长冻结）；
2. CBETS 发起跨境支付（幂等键 = pay:{order_id}）；
3. 买家钱包扣款（幂等键同上）；
4. 本地担保子钱包入账 + 合约创建（数据库唯一索引兜底重复入账）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..adapters import cbets, logistics, wallet
from ..config import settings
from ..db import connect, now_iso, tx
from ..errors import conflict, forbidden, not_found
from ..risk.decision import assess
from ..risk.model import get_model
from ..contract import escrow, state_machine
from .order_center import get_order, order_detail
from .tokenization import resolve_token


def _party(conn: sqlite3.Connection, party_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM parties WHERE party_id=?", (party_id,)).fetchone()
    if not row:
        raise not_found(f"交易主体 {party_id}")
    return row


def pay(*, order_id: str, token: str | None = None, wallet_id: str | None = None) -> dict:
    conn = connect()
    try:
        order = get_order(conn, order_id)
        if order["paid_at"]:
            return {"result": "already_paid", "order": order_detail(conn, order_id)}

        if order["status"] != "created":
            raise conflict("ORDER_NOT_PAYABLE", f"订单当前状态 {order['status']} 不可支付",
                           {"status": order["status"]})

        buyer = _party(conn, order["buyer_id"])
        payer_wallet = buyer["wallet_id"]
        if token:
            resolved = resolve_token(conn, token)
            if resolved["buyer_id"] != order["buyer_id"]:
                raise forbidden("TOKEN_OWNER_MISMATCH", "支付标记与订单买家不一致")
            payer_wallet = resolved["wallet_id"]
        elif wallet_id:
            payer_wallet = wallet_id

        risk = assess(conn, order, stage="pay")
        if risk["decision"] == "block":
            with tx(conn) as c:
                c.execute("UPDATE orders SET status='risk_blocked' WHERE order_id=?", (order_id,))
                c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                          ("order.risk_blocked", order_id,
                           json.dumps({"score": risk["final_score"],
                                       "rules": [h["rule_id"] for h in risk["hits"]]},
                                      ensure_ascii=False), now_iso()))
            raise forbidden("PAYMENT_RISK_BLOCKED", "支付被风控拦截",
                            {"risk": {k: risk[k] for k in
                                      ("final_score", "final_level", "reasons", "buyer_prob",
                                       "seller_prob")}})

        seller = _party(conn, order["seller_id"])
        idem = f"pay:{order_id}"
        trade = cbets().create_trade(
            order_id=order_id, amount_cents=order["amount_cents"], currency=order["currency"],
            payer_wallet=payer_wallet, payee_wallet=seller["wallet_id"],
            idempotency_key=idem, memo=order["sku_desc"],
        )
        debit = wallet().debit(wallet_id=payer_wallet, amount_cents=order["amount_cents"], ref_no=idem)

        hold_hours = settings.review_hold_hours if risk["decision"] == "review" else settings.escrow_hold_hours
        try:
            with tx(conn) as c:
                escrow.lock(c, seller_id=order["seller_id"], order_id=order_id,
                            amount_cents=order["amount_cents"], ref_no=trade["trade_no"])
                state_machine.create_contract(c, order_id=order_id, seller_id=order["seller_id"],
                                              amount_cents=order["amount_cents"],
                                              hold_hours=hold_hours)
                if risk["decision"] == "review":
                    c.execute("UPDATE orders SET review_flag=1 WHERE order_id=?", (order_id,))
                c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                          ("payment.captured", order_id,
                           json.dumps({"trade_no": trade["trade_no"],
                                       "wallet_tx": debit["tx_id"],
                                       "decision": risk["decision"]}, ensure_ascii=False), now_iso()))
        except sqlite3.IntegrityError:
            # 并发重复支付：账本唯一索引挡住第二次入账，返回当前状态即可
            return {"result": "already_paid", "order": order_detail(conn, order_id)}

        return {
            "result": "paid",
            "order": order_detail(conn, order_id),
            "payment": {"trade_no": trade["trade_no"], "wallet_tx_id": debit["tx_id"],
                        "buyer_balance_after_cents": debit.get("balance_cents")},
            "risk": {"decision": risk["decision"], "final_score": risk["final_score"],
                     "level": risk["final_level"], "reasons": risk["reasons"]},
            "escrow_hold_hours": hold_hours,
        }
    finally:
        conn.close()


def ship(*, order_id: str, carrier: str, tracking_no: str) -> dict:
    conn = connect()
    try:
        order = get_order(conn, order_id)
        if order["status"] != "paid_escrowed":
            raise conflict("ORDER_NOT_SHIPPABLE", f"订单当前状态 {order['status']} 不可发货",
                           {"status": order["status"]})
        shipment = logistics().create_shipment(order_id=order_id, carrier=carrier,
                                               tracking_no=tracking_no,
                                               order_created_at=order["created_at"])
        with tx(conn) as c:
            state_machine.transition(c, order_id=order_id, event="ship")
            c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                      ("order.shipped", order_id,
                       json.dumps({"tracking_no": tracking_no, "carrier": carrier,
                                   "pickup_time": shipment["pickup_time"]}, ensure_ascii=False),
                       now_iso()))
        return {"result": "shipped", "shipment": shipment, "order": order_detail(conn, order_id)}
    finally:
        conn.close()


def receive(*, order_id: str) -> dict:
    conn = connect()
    try:
        order = get_order(conn, order_id)
        if order["status"] != "shipped":
            raise conflict("ORDER_NOT_RECEIVABLE", f"订单当前状态 {order['status']} 不可确认收货",
                           {"status": order["status"]})
        with tx(conn) as c:
            state_machine.transition(c, order_id=order_id, event="receive")
            c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                      ("order.received", order_id, "{}", now_iso()))
        return {"result": "received", "order": order_detail(conn, order_id),
                "next": "确认收货后可释放担保资金；在售后窗口内仍可发起售后"}
    finally:
        conn.close()


def release(*, order_id: str, reviewer: str | None = None) -> dict:
    """正常履约释放：先做一次事后风控，再按锁定额全额释放。"""
    conn = connect()
    try:
        order = get_order(conn, order_id)
        contract = conn.execute("SELECT * FROM contracts WHERE order_id=?", (order_id,)).fetchone()
        if not contract:
            raise not_found(f"订单 {order_id} 的担保合约")
        if contract["status"] not in ("locked", "shipped", "received"):
            raise conflict("CONTRACT_NOT_RELEASABLE", f"合约状态 {contract['status']} 不可释放",
                           {"status": contract["status"]})
        if contract["status"] == "locked":
            raise conflict("NOT_SHIPPED_YET", "尚未发货，不能释放资金，请先发货或确认收货")

        # 放款闸门：担保期不是摆设。
        # 1) 风控复核单必须人工放款；2) 买家未确认收货时必须等担保期届满。
        if order["review_flag"] and not reviewer:
            raise forbidden("REVIEW_REQUIRED",
                            "该订单被风控标记为需复核，须人工复核后才能放款",
                            {"order_id": order_id, "hold_until": contract["hold_until"]})
        hold_until = contract["hold_until"]
        hold_expired = True
        if hold_until:
            expiry = datetime.fromisoformat(hold_until)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            hold_expired = datetime.now(timezone.utc) >= expiry
        received = contract["status"] == state_machine.STATUS_RECEIVED
        if not received and not hold_expired:
            raise conflict("ESCROW_HOLD_ACTIVE",
                           "买家未确认收货且担保期未届满，不能放款",
                           {"hold_until": hold_until, "contract_status": contract["status"],
                            "hint": "买家确认收货后可立即放款；未收货需等到 hold_until"})

        risk = assess(conn, order, stage="post")
        if risk["decision"] == "block" and not reviewer:
            raise forbidden("RELEASE_BLOCKED_BY_RISK", "事后风控拦截了资金释放，需人工复核",
                            {"risk": {"score": risk["final_score"], "reasons": risk["reasons"]}})

        remaining = contract["locked_cents"] - contract["released_cents"] - contract["refunded_cents"]
        with tx(conn) as c:
            info = escrow.release_to_seller(c, seller_id=order["seller_id"], order_id=order_id,
                                            gross_cents=remaining,
                                            ref_no=f"release:{order_id}")
            state_machine.transition(c, order_id=order_id, event="release")
            c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                      ("escrow.released", order_id,
                       json.dumps({"gross_cents": remaining, "fee_cents": info["fee_cents"],
                                   "reviewer": reviewer}, ensure_ascii=False), now_iso()))
        seller = _party(conn, order["seller_id"])
        credit = wallet().credit(wallet_id=seller["wallet_id"], amount_cents=info["net_cents"],
                                 ref_no=f"release:{order_id}")
        return {"result": "released", "gross_cents": remaining, "fee_cents": info["fee_cents"],
                "net_cents": info["net_cents"], "seller_wallet_tx": credit["tx_id"],
                "risk": {"decision": risk["decision"], "score": risk["final_score"],
                         "reasons": risk["reasons"]},
                "order": order_detail(conn, order_id)}
    finally:
        conn.close()


def models_info() -> dict:
    return {"buyer": get_model("buyer").metrics, "seller": get_model("seller").metrics,
            "buyer_version": get_model("buyer").version,
            "seller_version": get_model("seller").version}
