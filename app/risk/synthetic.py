"""合成训练数据生成器。

为什么要有它：真实交易样本拿不到，本地必须能训练出一个可复现、可评估的模型。
生成逻辑是确定的（固定随机种子），标签由隐变量驱动，特征与标签有可学习信号。
接真实数据后，把 generate() 换成从风控特征库读取即可，模型接口不变。
"""
from __future__ import annotations

import math
import random

BUYER_FEATURES = [
    "refund_7d", "refund_rate_90d", "chargeback_cnt", "account_age_days", "real_name_level",
    "device_change", "ip_country_mismatch", "address_flag", "amount_deviation_ratio",
    "hour", "cross_border", "fraud_tag",
]

SELLER_FEATURES = [
    "complaint_rate_30d", "fake_delivery_cnt", "logistics_offset_hours", "shop_age_days",
    "penalty_cnt", "blacklist_flag", "listing_violation_flag", "order_growth_ratio",
    "dispute_rate_30d", "avg_ship_hours",
]


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _buyer_sample(rng: random.Random) -> tuple[dict, int]:
    latent = rng.gauss(0, 1)
    y = 1 if latent > 0.95 else 0
    f = {
        "refund_7d": max(0, round(0.6 + latent * 2.6 + rng.gauss(0, 0.7))),
        "refund_rate_90d": round(_clip(0.08 + latent * 0.16 + rng.gauss(0, 0.04), 0, 1), 3),
        "chargeback_cnt": max(0, round(0.15 + latent * 0.55 + rng.gauss(0, 0.3))),
        "account_age_days": max(1, round(380 - latent * 160 + rng.gauss(0, 80))),
        "real_name_level": rng.choices([0, 1, 2], weights=[0.02 + 0.06 * max(latent, 0), 0.2, 0.78])[0],
        "device_change": 1 if rng.random() < 0.12 + 0.25 * max(latent, 0) else 0,
        "ip_country_mismatch": 1 if rng.random() < 0.1 + 0.3 * max(latent, 0) else 0,
        "address_flag": 1 if rng.random() < 0.05 + 0.3 * max(latent, 0) else 0,
        "amount_deviation_ratio": round(_clip(1.0 + latent * 1.6 + rng.gauss(0, 0.8), 0.2, 12), 2),
        "hour": rng.choice([rng.randrange(0, 24), rng.randrange(2, 6)] if latent > 0.6
                           else [rng.randrange(7, 23)]),
        "cross_border": 1,
        "fraud_tag": 1 if rng.random() < 0.03 + 0.25 * max(latent - 0.8, 0) else 0,
    }
    return f, y


def _seller_sample(rng: random.Random) -> tuple[dict, int]:
    latent = rng.gauss(0, 1)
    y = 1 if latent > 1.05 else 0
    f = {
        "complaint_rate_30d": round(_clip(0.05 + latent * 0.10 + rng.gauss(0, 0.03), 0, 1), 3),
        "fake_delivery_cnt": max(0, round(0.1 + latent * 0.7 + rng.gauss(0, 0.4))),
        "logistics_offset_hours": round(_clip(8 - latent * 8 + rng.gauss(0, 3), -6, 96), 1),
        "shop_age_days": max(3, round(520 - latent * 220 + rng.gauss(0, 120))),
        "penalty_cnt": max(0, round(0.2 + latent * 0.8 + rng.gauss(0, 0.5))),
        "blacklist_flag": 1 if rng.random() < 0.02 + 0.2 * max(latent - 0.9, 0) else 0,
        "listing_violation_flag": 1 if rng.random() < 0.02 + 0.22 * max(latent - 0.9, 0) else 0,
        "order_growth_ratio": round(_clip(1.0 + latent * 1.2 + rng.gauss(0, 0.5), 0.3, 8), 2),
        "dispute_rate_30d": round(_clip(0.03 + latent * 0.08 + rng.gauss(0, 0.03), 0, 1), 3),
        "avg_ship_hours": round(_clip(24 + latent * 18 + rng.gauss(0, 10), 2, 240), 1),
    }
    return f, y


def generate(kind: str, n: int = 4000, seed: int = 20261114) -> list[tuple[dict, int]]:
    rng = random.Random(seed)
    fn = _buyer_sample if kind == "buyer" else _seller_sample
    return [fn(rng) for _ in range(n)]


def feature_names(kind: str) -> list[str]:
    return BUYER_FEATURES if kind == "buyer" else SELLER_FEATURES
