"""合约、担保账本、售后纠纷、存证审计接口。"""
from __future__ import annotations

import json
import sqlite3
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..adapters import chain
from ..audit import merkle_root, verify_chain
from ..contract import after_sales, escrow, state_machine
from ..db import tx
from ..errors import bad_request, not_found
from .deps import get_conn, require_admin

router = APIRouter()


class DisputeIn(BaseModel):
    dtype: str = Field(description="no_reason_return / partial_damage / fake_delivery / "
                                  "malicious_refund / logistics_loss")
    claimant: str = Field(default="buyer")
    evidence: dict = Field(default_factory=dict)


class ArbitrateIn(BaseModel):
    refund_cents: int = Field(ge=0)
    release_cents: int = Field(ge=0)
    reviewer: str
    note: str = ""


@router.get("/contract/{order_id}", summary="查询合约状态机")
def contract(order_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    c = state_machine.get_contract(conn, order_id)
    allowed = state_machine.TRANSITIONS.get(c["status"], {})
    return {"contract": c, "allowed_events": sorted(allowed.keys()),
            "terminal": c["status"] in state_machine.TERMINAL}


@router.get("/escrow/{seller_id}", summary="商户担保子钱包余额与流水")
def escrow_account(seller_id: str, limit: int = Query(50, le=200),
                  conn: sqlite3.Connection = Depends(get_conn)):
    acc = escrow.account(conn, seller_id)
    entries = conn.execute(
        "SELECT seq,entry_type,amount_cents,locked_after,settled_after,order_id,ref_no,memo,"
        "entry_hash,created_at FROM ledger_entries WHERE seller_id=? ORDER BY seq DESC LIMIT ?",
        (seller_id, limit)).fetchall()
    return {"account": acc, "entries": [dict(e) for e in entries],
            "reconcile": escrow.reconcile(conn, seller_id)}


@router.post("/orders/{order_id}/dispute", status_code=201, summary="发起售后（五类场景）")
def open_dispute(order_id: str, body: DisputeIn):
    return after_sales.open_dispute(order_id=order_id, dtype=body.dtype,
                                    claimant=body.claimant, evidence=body.evidence)


@router.get("/disputes/{dispute_id}", summary="纠纷详情")
def dispute(dispute_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    return after_sales.get_dispute(conn, dispute_id)


@router.get("/policies", summary="五类售后场景的举证要求")
def policies():
    return after_sales.POLICIES


@router.post("/disputes/{dispute_id}/arbitrate", summary="人工仲裁裁决（管理员）",
             dependencies=[Depends(require_admin)])
def arbitrate(dispute_id: str, body: ArbitrateIn):
    if body.refund_cents <= 0 and body.release_cents <= 0:
        raise bad_request("裁决金额不能为零")
    return after_sales.settle_for_arbitration(
        dispute_id=dispute_id, refund_cents=body.refund_cents,
        release_cents=body.release_cents, reviewer=body.reviewer, note=body.note,
    )


@router.get("/audit/verify", summary="校验账本哈希链")
def audit_verify(conn: sqlite3.Connection = Depends(get_conn)):
    return verify_chain(conn)


@router.get("/audit/entries", summary="账本明细")
def audit_entries(limit: int = Query(100, le=500), conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM ledger_entries ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/audit/anchor", summary="把未锚定账本的 Merkle 根写入存证层（管理员）",
             dependencies=[Depends(require_admin)])
def audit_anchor(conn: sqlite3.Connection = Depends(get_conn)):
    last = conn.execute("SELECT MAX(entry_to_seq) AS s FROM anchors").fetchone()
    from_seq = (last["s"] or 0) + 1
    rows = conn.execute("SELECT seq,entry_hash FROM ledger_entries WHERE seq>=? ORDER BY seq",
                        (from_seq,)).fetchall()
    if not rows:
        return {"result": "nothing_to_anchor"}
    to_seq = rows[-1]["seq"]
    root = merkle_root([r["entry_hash"] for r in rows])
    batch_id = "AN" + uuid.uuid4().hex[:12].upper()
    result = chain().anchor(batch_id=batch_id, merkle_root=root, from_seq=from_seq, to_seq=to_seq)
    return {"batch_id": batch_id, "merkle_root": root, "from_seq": from_seq,
            "to_seq": to_seq, "entries": len(rows), "anchor": result}


@router.get("/audit/anchors", summary="已上链锚定记录")
def anchors(conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM anchors ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"items": [dict(r) for r in rows]}
