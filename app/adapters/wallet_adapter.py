"""Mock 数字人民币钱包适配器。

真实替换点：数字人民币商户开放平台 / 运营机构钱包接口。
Mock 版用 wallet_balances 表镜像外部钱包余额；wallet_txns 表保证同一 ref_no 的
扣款/入账幂等，网络重试不会重复记一次账。
"""
from __future__ import annotations

import hashlib

from ..db import connect, now_iso, tx
from ..errors import bad_request
from .base import WalletAdapter


def _tx_id(prefix: str, payload: str) -> str:
    return prefix + hashlib.sha256(payload.encode()).hexdigest()[:24]


class MockWalletAdapter(WalletAdapter):
    def kyc_level(self, wallet_id: str) -> int:
        conn = connect()
        try:
            row = conn.execute("SELECT kyc_level FROM wallet_balances WHERE wallet_id=?",
                               (wallet_id,)).fetchone()
            return int(row["kyc_level"]) if row else 0
        finally:
            conn.close()

    def _apply(self, *, wallet_id: str, amount_cents: int, ref_no: str, op: str) -> dict:
        conn = connect()
        try:
            done = conn.execute("SELECT tx_id FROM wallet_txns WHERE ref_no=? AND op=?",
                                (ref_no, op)).fetchone()
            if done:
                row = conn.execute("SELECT balance_cents FROM wallet_balances WHERE wallet_id=?",
                                   (wallet_id,)).fetchone()
                return {"ok": True, "idempotent_replay": True, "tx_id": done["tx_id"],
                        "balance_cents": row["balance_cents"] if row else 0}
            row = conn.execute("SELECT balance_cents FROM wallet_balances WHERE wallet_id=?",
                               (wallet_id,)).fetchone()
            if op == "debit":
                if not row:
                    raise bad_request(f"钱包 {wallet_id} 不存在")
                if row["balance_cents"] < amount_cents:
                    raise bad_request("钱包余额不足", {"balance_cents": row["balance_cents"],
                                                     "required_cents": amount_cents})
                new_balance = row["balance_cents"] - amount_cents
            else:
                new_balance = (row["balance_cents"] if row else 0) + amount_cents
            tx_id = _tx_id("WD" if op == "debit" else "WC", f"{wallet_id}{ref_no}{amount_cents}")
            with tx(conn) as c:
                if row:
                    c.execute("UPDATE wallet_balances SET balance_cents=?, updated_at=? WHERE wallet_id=?",
                              (new_balance, now_iso(), wallet_id))
                else:
                    c.execute("INSERT INTO wallet_balances(wallet_id,balance_cents,kyc_level,updated_at)"
                              " VALUES(?,?,1,?)", (wallet_id, new_balance, now_iso()))
                c.execute("INSERT INTO wallet_txns(tx_id,ref_no,op,wallet_id,amount_cents,created_at)"
                          " VALUES(?,?,?,?,?,?)",
                          (tx_id, ref_no, op, wallet_id, amount_cents, now_iso()))
                c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                          (f"wallet.{op}", ref_no,
                           '{"wallet_id":"%s","amount_cents":%d}' % (wallet_id, amount_cents), now_iso()))
            return {"ok": True, "balance_cents": new_balance, "tx_id": tx_id}
        finally:
            conn.close()

    def debit(self, *, wallet_id: str, amount_cents: int, ref_no: str) -> dict:
        return self._apply(wallet_id=wallet_id, amount_cents=amount_cents, ref_no=ref_no, op="debit")

    def credit(self, *, wallet_id: str, amount_cents: int, ref_no: str) -> dict:
        return self._apply(wallet_id=wallet_id, amount_cents=amount_cents, ref_no=ref_no, op="credit")
