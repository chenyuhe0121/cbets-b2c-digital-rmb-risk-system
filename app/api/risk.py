"""风控接口：事前预览、规则配置、模型信息与重训练。"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..db import tx
from ..errors import bad_request, not_found
from ..gateway.order_center import get_order
from ..risk.decision import assess
from ..risk.features import buyer_features, seller_features
from ..risk.model import get_model, model_info, train_and_save
from ..risk.rules import load_rules
from .deps import get_conn, require_admin

router = APIRouter()


class RulePatch(BaseModel):
    enabled: bool | None = None
    action: str | None = Field(default=None, pattern="^(block|review)$")
    weight: float | None = Field(default=None, ge=0, le=1)
    threshold: float | None = None


@router.get("/preview", summary="下单页事前风控，不冻结资金")
def preview(order_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    order = get_order(conn, order_id)
    return assess(conn, order, stage="pre", persist=True)


@router.get("/records", summary="风控记录")
def records(order_id: str | None = None, limit: int = Query(50, le=200),
            conn: sqlite3.Connection = Depends(get_conn)):
    sql = "SELECT * FROM risk_records"
    args: list = []
    if order_id:
        sql += " WHERE order_id=?"
        args.append(order_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["hit_rules"] = json.loads(d["hit_rules"])
        d["reasons"] = json.loads(d["reasons"])
        d.pop("contributions", None)
        out.append(d)
    return {"items": out}


@router.get("/rules", summary="规则库（含已停用）")
def rules(conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": load_rules(conn, include_disabled=True)}


@router.patch("/rules/{rule_id}", summary="启用/停用/调整规则（管理员）", dependencies=[Depends(require_admin)])
def patch_rule(rule_id: str, body: RulePatch, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
    if not row:
        raise not_found(f"规则 {rule_id}")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise bad_request("没有需要修改的字段")
    if "threshold" in updates:
        updates["threshold"] = json.dumps(updates["threshold"])
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    with tx(conn) as c:
        for key, value in updates.items():
            c.execute(f"UPDATE rules SET {key}=? WHERE rule_id=?", (value, rule_id))
    return {"rule_id": rule_id, "updated": list(updates)}


@router.get("/models", summary="模型版本与离线指标")
def models():
    return {"buyer": model_info("buyer"), "seller": model_info("seller")}


@router.post("/models/train", summary="用当前数据集重训练（管理员）", dependencies=[Depends(require_admin)])
def retrain(kind: str = Query("buyer", pattern="^(buyer|seller)$"), n: int = Query(4000, ge=500, le=20000)):
    model = train_and_save(kind, n=n)
    return {"kind": kind, "version": model.version, "metrics": model.metrics}


@router.get("/features", summary="两个模型的输入特征（排查用）")
def features(order_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    order = get_order(conn, order_id)
    return {"buyer": buyer_features(conn, order), "seller": seller_features(conn, order),
            "buyer_prob": get_model("buyer").predict_proba(buyer_features(conn, order)),
            "seller_prob": get_model("seller").predict_proba(seller_features(conn, order))}
