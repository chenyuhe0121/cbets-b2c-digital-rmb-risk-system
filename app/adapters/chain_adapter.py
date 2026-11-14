"""Mock 上链适配器：把账本 Merkle 根写到 anchors 表并生成可校验的 tx_id。

真实替换点：FISCO BCOS、蚂蚁链、成方链的 SDK。接口只有 anchor 一个方法，
上链失败不应该阻塞资金流程，调用方按"先记账后锚定"处理。
"""
from __future__ import annotations

import hashlib
import json

from ..db import connect, now_iso, tx
from .base import ChainAdapter


class MockChainAdapter(ChainAdapter):
    def anchor(self, *, batch_id: str, merkle_root: str, from_seq: int, to_seq: int) -> dict:
        tx_id = "0x" + hashlib.sha256(f"{batch_id}{merkle_root}".encode()).hexdigest()
        conn = connect()
        try:
            with tx(conn) as c:
                c.execute(
                    "INSERT OR REPLACE INTO anchors(anchor_id,merkle_root,entry_from_seq,entry_to_seq,"
                    "chain,tx_id,created_at) VALUES(?,?,?,?,?,?,?)",
                    (batch_id, merkle_root, from_seq, to_seq, "mock-local", tx_id, now_iso()),
                )
                c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                          ("chain.anchored", batch_id,
                           json.dumps({"merkle_root": merkle_root, "tx_id": tx_id,
                                       "from_seq": from_seq, "to_seq": to_seq}, ensure_ascii=False),
                           now_iso()))
            return {"tx_id": tx_id, "chain": "mock-local", "block_height": from_seq + to_seq + 1}
        finally:
            conn.close()
