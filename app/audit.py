"""存证层：账本条目哈希链 + Merkle 根锚定。

说明：这是可验证的防篡改账本（append-only + 哈希链），不等同于区块链。
上链适配器（chain_adapter）负责把 Merkle 根写到联盟链，当前默认用 Mock。
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

import sqlite3

GENESIS = "0" * 64


def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def append_entry(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    seller_id: str,
    order_id: str | None,
    entry_type: str,
    amount_cents: int,
    locked_after: int,
    settled_after: int,
    ref_no: str | None,
    memo: str,
    created_at: str,
) -> str:
    """在账本尾部追加一条，返回本条 entry_hash。必须在显式事务里调用。"""
    prev = conn.execute("SELECT entry_hash FROM ledger_entries ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = prev["entry_hash"] if prev else GENESIS
    payload = {
        "entry_id": entry_id,
        "seller_id": seller_id,
        "order_id": order_id,
        "entry_type": entry_type,
        "amount_cents": amount_cents,
        "locked_after": locked_after,
        "settled_after": settled_after,
        "ref_no": ref_no,
        "created_at": created_at,
    }
    entry_hash = _sha(prev_hash + canonical(payload))
    conn.execute(
        "INSERT INTO ledger_entries(entry_id,seller_id,order_id,entry_type,amount_cents,locked_after,"
        "settled_after,ref_no,memo,prev_hash,entry_hash,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (entry_id, seller_id, order_id, entry_type, amount_cents, locked_after, settled_after,
         ref_no, memo, prev_hash, entry_hash, created_at),
    )
    return entry_hash


def verify_chain(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT seq,entry_id,seller_id,order_id,entry_type,amount_cents,locked_after,settled_after,"
        "ref_no,created_at,prev_hash,entry_hash FROM ledger_entries ORDER BY seq ASC"
    ).fetchall()
    prev_hash = GENESIS
    for r in rows:
        if r["prev_hash"] != prev_hash:
            return {"valid": False, "broken_at_seq": r["seq"], "reason": "prev_hash 不匹配"}
        payload = {
            "entry_id": r["entry_id"], "seller_id": r["seller_id"], "order_id": r["order_id"],
            "entry_type": r["entry_type"], "amount_cents": r["amount_cents"],
            "locked_after": r["locked_after"], "settled_after": r["settled_after"],
            "ref_no": r["ref_no"], "created_at": r["created_at"],
        }
        expected = _sha(prev_hash + canonical(payload))
        if expected != r["entry_hash"]:
            return {"valid": False, "broken_at_seq": r["seq"], "reason": "entry_hash 被改动"}
        prev_hash = r["entry_hash"]
    return {"valid": True, "entries": len(rows), "head_hash": prev_hash}


def merkle_root(leaves: Iterable[str]) -> str:
    level = list(leaves)
    if not level:
        return GENESIS
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            nxt.append(_sha(left + right))
        level = nxt
    return level[0]
