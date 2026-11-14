"""风险决策中心：规则 + 模型加权融合，输出可解释的最终结论。

融合公式：final = 1 - (1 - p_model) * (1 - severity_rules)
- p_model 取买家、卖家两个模型概率的较大值（最坏情况优先）；
- severity_rules 取命中的 review 类规则的最大权重；
- 硬拦截规则（block）直接短路，不再看分数。
"""
from __future__ import annotations

import json

import sqlite3

from ..adapters import commerce, cbets
from ..config import settings
from ..db import now_iso, tx
from .features import buyer_features, seller_features
from .model import get_model
from .rules import evaluate

LEVEL_BY_DECISION = {"block": "high", "review": "medium", "pass": "low"}


def _reason_from_hits(hits: list[dict]) -> list[dict]:
    out = []
    for h in hits:
        out.append({
            "source": "rule", "code": h["rule_id"], "message": h["name"],
            "detail": f"{h['field']}={h['value']}，阈值 {h['threshold']}",
            "action": h["action"],
        })
    return out


def assess(conn: sqlite3.Connection, order, stage: str = "pay", persist: bool = True) -> dict:
    bf = buyer_features(conn, order)
    sf = seller_features(conn, order)
    amount = int(order["amount_cents"])

    buyer_action, buyer_hits = evaluate(bf, amount, conn, "buyer")
    seller_action, seller_hits = evaluate(sf, amount, conn, "seller")
    actions = [buyer_action, seller_action]
    rule_action = "block" if "block" in actions else ("review" if "review" in actions else "pass")
    hits = buyer_hits + seller_hits

    buyer_model = get_model("buyer")
    seller_model = get_model("seller")
    buyer_prob = buyer_model.predict_proba(bf)
    seller_prob = seller_model.predict_proba(sf)

    severity = max([h["weight"] for h in hits if h["action"] == "review"], default=0.0)
    p_model = max(buyer_prob, seller_prob)
    final_score = 1.0 - (1.0 - p_model) * (1.0 - severity)

    if rule_action == "block":
        decision = "block"
    elif final_score >= settings.block_threshold:
        decision = "block"
    elif rule_action == "review" or final_score >= settings.review_threshold:
        decision = "review"
    else:
        decision = "pass"

    reasons = _reason_from_hits(hits)
    for kind, prob, model, feats in (("buyer", buyer_prob, buyer_model, bf),
                                     ("seller", seller_prob, seller_model, sf)):
        if prob >= settings.review_threshold:
            for c in model.explain(feats, top_k=3):
                if c["contribution"] > 0.15:
                    reasons.append({
                        "source": "model", "code": f"{kind.upper()}_MODEL",
                        "message": f"{kind}模型风险贡献项：{c['feature']}",
                        "detail": f"值 {c['value']}，贡献 +{c['contribution']}",
                    })
    if not reasons:
        reasons.append({"source": "model", "code": "LOW_RISK",
                        "message": "规则与模型均未命中风险项"})

    record = {
        "order_id": order["order_id"],
        "stage": stage,
        "buyer_prob": round(buyer_prob, 4),
        "seller_prob": round(seller_prob, 4),
        "rule_action": rule_action,
        "hits": hits,
        "final_score": round(final_score, 4),
        "final_level": LEVEL_BY_DECISION[decision],
        "decision": decision,
        "reasons": reasons,
        "model_version": f"{buyer_model.version}/{seller_model.version}",
        "buyer_features": bf,
        "seller_features": sf,
        "thresholds": {"block": settings.block_threshold, "review": settings.review_threshold},
    }

    if persist:
        with tx(conn) as c:
            c.execute(
                "INSERT INTO risk_records(order_id,stage,buyer_prob,seller_prob,rule_action,hit_rules,"
                "contributions,final_score,final_level,decision,reasons,model_version,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (order["order_id"], stage, record["buyer_prob"], record["seller_prob"], rule_action,
                 json.dumps(hits, ensure_ascii=False),
                 json.dumps({"buyer": buyer_model.explain(bf, 8),
                             "seller": seller_model.explain(sf, 8)}, ensure_ascii=False),
                 record["final_score"], record["final_level"], decision,
                 json.dumps(reasons, ensure_ascii=False), record["model_version"], now_iso()),
            )
            c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                      ("risk.assessed", order["order_id"],
                       json.dumps({"stage": stage, "decision": decision,
                                   "score": record["final_score"], "rules": [h["rule_id"] for h in hits]},
                                  ensure_ascii=False), now_iso()))

    try:
        commerce().notify_decision(order_id=order["order_id"], decision=decision,
                                   reasons=[r["message"] for r in reasons])
    except Exception:  # 平台回写失败不影响主流程，事件里可重放
        pass
    return record
