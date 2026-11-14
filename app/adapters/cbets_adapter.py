"""Mock 数币达（CBETS）适配器。

设计成真实网关的样子：
- 幂等键唯一，重复提交返回同一笔 trade_no；
- 交易状态落在 cbets_trades 表，可查、可退；
- 退款必须引用原交易，且累计退款额不得超过原交易额（多次部分退款会累加）。

真实替换点：银行提供的数币达网关 SDK（create_trade / query_trade / refund 三个方法）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from ..db import connect, now_iso, tx
from ..errors import bad_request, conflict, not_found
from .base import CBETSAdapter


class MockCBETSAdapter(CBETSAdapter):
    def create_trade(self, *, order_id: str, amount_cents: int, currency: str,
                     payer_wallet: str, payee_wallet: str, idempotency_key: str,
                     memo: str = "") -> dict:
        conn = connect()
        try:
            existing = conn.execute(
                "SELECT * FROM cbets_trades WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing:
                if existing["status"] == "settled":
                    return {"trade_no": existing["trade_no"], "status": "settled",
                            "created_at": existing["created_at"], "idempotent_replay": True}
                raise conflict("TRADE_NOT_SETTLED", "同一幂等键的交易尚未成功，请查询原交易状态")

            trade_no = "CB" + hashlib.sha256(f"{order_id}{idempotency_key}".encode()).hexdigest()[:20].upper()
            payload = {"order_id": order_id, "amount_cents": amount_cents, "currency": currency,
                       "payer": payer_wallet, "payee": payee_wallet, "memo": memo}
            with tx(conn) as c:
                c.execute(
                    "INSERT INTO cbets_trades(trade_no,order_id,idempotency_key,amount_cents,status,raw,"
                    "created_at) VALUES(?,?,?,?,'settled',?,?)",
                    (trade_no, order_id, idempotency_key, amount_cents,
                     json.dumps(payload, ensure_ascii=False), now_iso()),
                )
                c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                          ("cbets.trade_settled", order_id,
                           json.dumps({"trade_no": trade_no}, ensure_ascii=False), now_iso()))
            return {"trade_no": trade_no, "status": "settled", "created_at": now_iso()}
        finally:
            conn.close()

    def query_trade(self, trade_no: str) -> dict:
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM cbets_trades WHERE trade_no=?", (trade_no,)).fetchone()
            if not row:
                raise not_found(f"CBETS 交易 {trade_no}")
            return {"trade_no": row["trade_no"], "status": row["status"],
                    "amount_cents": row["amount_cents"],
                    "refunded_cents": row["refunded_cents"] or 0,
                    "created_at": row["created_at"]}
        finally:
            conn.close()

    def refund(self, *, trade_no: str, amount_cents: int, idempotency_key: str, reason: str = "") -> dict:
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM cbets_trades WHERE trade_no=?", (trade_no,)).fetchone()
            if not row:
                raise not_found(f"CBETS 交易 {trade_no}")
            if row["status"] == "refunded":
                raise conflict("ALREADY_REFUNDED", "该交易已全额退款")
            refunded_so_far = row["refunded_cents"] or 0
            if amount_cents <= 0:
                raise bad_request("退款金额必须大于 0 分")
            if refunded_so_far + amount_cents > row["amount_cents"]:
                raise bad_request("累计退款金额不得超过原交易金额",
                                  {"trade_amount_cents": row["amount_cents"],
                                   "already_refunded_cents": refunded_so_far,
                                   "requested_cents": amount_cents,
                                   "remaining_refundable_cents": row["amount_cents"] - refunded_so_far})
            refund_no = "RF" + hashlib.sha256(f"{trade_no}{idempotency_key}".encode()).hexdigest()[:20].upper()
            total_refunded = refunded_so_far + amount_cents
            with tx(conn) as c:
                status = "refunded" if total_refunded == row["amount_cents"] else "partial_refunded"
                c.execute("UPDATE cbets_trades SET status=?, refunded_cents=? WHERE trade_no=?",
                          (status, total_refunded, trade_no))
                c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                          ("cbets.refunded", row["order_id"],
                           json.dumps({"refund_no": refund_no, "amount_cents": amount_cents,
                                       "total_refunded_cents": total_refunded,
                                       "reason": reason}, ensure_ascii=False), now_iso()))
            return {"refund_no": refund_no, "status": status, "amount_cents": amount_cents,
                    "total_refunded_cents": total_refunded}
        finally:
            conn.close()
