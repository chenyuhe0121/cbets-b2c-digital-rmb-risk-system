"""担保子钱包记账。

资金口径：
- locked_cents：当前被合约锁定的货款（尚未释放也未退款）
- settled_cents：已释放到商户结算余额的部分（净额，扣平台费）
- refunded_cents：已退回买家的部分

所有函数都要求调用方处在显式事务中（db.tx），不自己开事务。
账本每条都写哈希链（audit.append_entry），可校验。
"""
from __future__ import annotations

import sqlite3
import uuid

from ..audit import append_entry
from ..config import settings
from ..db import now_iso
from ..errors import conflict


def _entry_id() -> str:
    return "LE" + uuid.uuid4().hex[:16].upper()


def ensure_account(conn: sqlite3.Connection, seller_id: str) -> None:
    row = conn.execute("SELECT seller_id FROM escrow_accounts WHERE seller_id=?", (seller_id,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO escrow_accounts(seller_id,currency,locked_cents,settled_cents,refunded_cents,"
            "frozen,updated_at) VALUES(?, 'CNY', 0, 0, 0, 0, ?)", (seller_id, now_iso())
        )


def account(conn: sqlite3.Connection, seller_id: str) -> dict:
    row = conn.execute("SELECT * FROM escrow_accounts WHERE seller_id=?", (seller_id,)).fetchone()
    if not row:
        return {"seller_id": seller_id, "locked_cents": 0, "settled_cents": 0,
                "refunded_cents": 0, "frozen": 0, "frozen_reason": None}
    return dict(row)


def _write(conn: sqlite3.Connection, *, seller_id: str, order_id: str | None, entry_type: str,
           amount_cents: int, locked_after: int, settled_after: int, ref_no: str | None,
           memo: str) -> str:
    return append_entry(
        conn, entry_id=_entry_id(), seller_id=seller_id, order_id=order_id, entry_type=entry_type,
        amount_cents=amount_cents, locked_after=locked_after, settled_after=settled_after,
        ref_no=ref_no, memo=memo, created_at=now_iso(),
    )


def lock(conn: sqlite3.Connection, *, seller_id: str, order_id: str, amount_cents: int,
         ref_no: str, memo: str = "支付成功，货款进入担保子钱包") -> dict:
    ensure_account(conn, seller_id)
    acc = account(conn, seller_id)
    locked_after = acc["locked_cents"] + amount_cents
    conn.execute(
        "UPDATE escrow_accounts SET locked_cents=?, updated_at=? WHERE seller_id=?",
        (locked_after, now_iso(), seller_id),
    )
    _write(conn, seller_id=seller_id, order_id=order_id, entry_type="LOCK",
           amount_cents=amount_cents, locked_after=locked_after,
           settled_after=acc["settled_cents"], ref_no=ref_no, memo=memo)
    return {"locked_cents": locked_after, "settled_cents": acc["settled_cents"]}


def release_to_seller(conn: sqlite3.Connection, *, seller_id: str, order_id: str, gross_cents: int,
                      ref_no: str, memo: str = "履约完成，货款释放给商户") -> dict:
    ensure_account(conn, seller_id)
    acc = account(conn, seller_id)
    if acc["frozen"]:
        raise conflict("SELLER_ACCOUNT_FROZEN",
                       "商户担保账户处于冻结状态，暂不能释放资金",
                       {"reason": acc["frozen_reason"]})
    if gross_cents <= 0 or gross_cents > acc["locked_cents"]:
        raise conflict("INSUFFICIENT_LOCKED_FUNDS", "释放金额超过该商户当前锁定余额",
                       {"locked_cents": acc["locked_cents"], "requested_cents": gross_cents})
    fee = gross_cents * settings.platform_fee_bps // 10000
    net = gross_cents - fee
    locked_after = acc["locked_cents"] - gross_cents
    settled_after = acc["settled_cents"] + net
    conn.execute(
        "UPDATE escrow_accounts SET locked_cents=?, settled_cents=?, updated_at=? WHERE seller_id=?",
        (locked_after, settled_after, now_iso(), seller_id),
    )
    # 合约侧同步记账，供对账与指标使用；版本号由状态机统一维护
    conn.execute("UPDATE contracts SET released_cents=released_cents+?, updated_at=? WHERE order_id=?",
                 (gross_cents, now_iso(), order_id))
    _write(conn, seller_id=seller_id, order_id=order_id, entry_type="RELEASE",
           amount_cents=-gross_cents, locked_after=locked_after, settled_after=settled_after,
           ref_no=ref_no, memo=f"{memo}（平台费 {fee} 分）")
    return {"fee_cents": fee, "net_cents": net, "locked_cents": locked_after,
            "settled_cents": settled_after}


def refund_to_buyer(conn: sqlite3.Connection, *, seller_id: str, order_id: str, amount_cents: int,
                    ref_no: str, memo: str = "退款至买家数字人民币钱包") -> dict:
    ensure_account(conn, seller_id)
    acc = account(conn, seller_id)
    if amount_cents <= 0 or amount_cents > acc["locked_cents"]:
        raise conflict("INSUFFICIENT_LOCKED_FUNDS", "退款金额超过该商户当前锁定余额",
                       {"locked_cents": acc["locked_cents"], "requested_cents": amount_cents})
    locked_after = acc["locked_cents"] - amount_cents
    refunded_after = acc["refunded_cents"] + amount_cents
    conn.execute(
        "UPDATE escrow_accounts SET locked_cents=?, refunded_cents=?, updated_at=? WHERE seller_id=?",
        (locked_after, refunded_after, now_iso(), seller_id),
    )
    conn.execute("UPDATE contracts SET refunded_cents=refunded_cents+?, updated_at=? WHERE order_id=?",
                 (amount_cents, now_iso(), order_id))
    _write(conn, seller_id=seller_id, order_id=order_id, entry_type="REFUND",
           amount_cents=-amount_cents, locked_after=locked_after,
           settled_after=acc["settled_cents"], ref_no=ref_no, memo=memo)
    return {"locked_cents": locked_after, "refunded_cents": refunded_after}


def freeze(conn: sqlite3.Connection, *, seller_id: str, reason: str) -> dict:
    ensure_account(conn, seller_id)
    acc = account(conn, seller_id)
    conn.execute("UPDATE escrow_accounts SET frozen=1, frozen_reason=?, updated_at=? WHERE seller_id=?",
                 (reason, now_iso(), seller_id))
    _write(conn, seller_id=seller_id, order_id=None, entry_type="FREEZE", amount_cents=0,
           locked_after=acc["locked_cents"], settled_after=acc["settled_cents"],
           ref_no=None, memo=f"账户冻结：{reason}")
    return {"frozen": 1, "reason": reason}


def unfreeze(conn: sqlite3.Connection, *, seller_id: str, reason: str = "人工解除") -> dict:
    acc = account(conn, seller_id)
    conn.execute("UPDATE escrow_accounts SET frozen=0, frozen_reason=NULL, updated_at=? WHERE seller_id=?",
                 (now_iso(), seller_id))
    _write(conn, seller_id=seller_id, order_id=None, entry_type="UNFREEZE", amount_cents=0,
           locked_after=acc["locked_cents"], settled_after=acc["settled_cents"],
           ref_no=None, memo=f"账户解冻：{reason}")
    return {"frozen": 0}


def reconcile(conn: sqlite3.Connection, seller_id: str) -> dict:
    """核对：账户 locked_cents 应等于所有未结清合约的剩余锁定额之和。"""
    acc = account(conn, seller_id)
    rows = conn.execute(
        "SELECT locked_cents, released_cents, refunded_cents FROM contracts "
        "WHERE seller_id=? AND status NOT IN ('released','refunded','partial_refunded','closed')",
        (seller_id,),
    ).fetchall()
    expected = sum(r["locked_cents"] - r["released_cents"] - r["refunded_cents"] for r in rows)
    return {"seller_id": seller_id, "account_locked_cents": acc["locked_cents"],
            "contract_locked_cents": expected, "consistent": expected == acc["locked_cents"]}
