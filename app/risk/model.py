"""风控评分模型：纯 Python 逻辑回归，可训练、可解释、无重型依赖。

为什么不用 GAN：表格特征的风控任务是二分类，判别器拿来当分类器不属于 GAN，
而且没有训练代码的随机权重输出没有业务含义。这里用逻辑回归，
每个特征对分数的贡献可以直接落到解释里，监管沟通和申诉都好用。
接真实数据后换成 LightGBM/XGBoost 只需替换 fit/predict 实现。
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..config import settings
from .synthetic import feature_names, generate

_cache: dict[str, "LogisticModel"] = {}


def _sigmoid(z: float) -> float:
    if z > 36:
        return 1.0
    if z < -36:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def auc_score(y_true: list[int], scores: list[float]) -> float:
    """rank 法计算 AUC，没有正负样本时返回 0.5。"""
    pos = sum(y_true)
    neg = len(y_true) - pos
    if pos == 0 or neg == 0:
        return 0.5
    pairs = sorted(zip(scores, y_true), key=lambda t: t[0])
    rank_sum = 0.0
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j + 2) / 2  # 1-based 平均秩
        rank_sum += avg_rank * sum(1 for k in range(i, j + 1) if pairs[k][1] == 1)
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


class LogisticModel:
    def __init__(self, kind: str, names: list[str], mean: list[float], std: list[float],
                 weights: list[float], bias: float, metrics: dict | None = None, version: str = ""):
        self.kind = kind
        self.names = names
        self.mean = mean
        self.std = std
        self.weights = weights
        self.bias = bias
        self.metrics = metrics or {}
        self.version = version

    # ---- 训练 ----
    @classmethod
    def fit(cls, kind: str, data: Iterable[tuple[dict, int]], epochs: int = 700,
            lr: float = 0.28, l2: float = 1e-3) -> "LogisticModel":
        names = feature_names(kind)
        rows = list(data)
        xs = [[float(f.get(n, 0.0)) for n in names] for f, _ in rows]
        ys = [int(y) for _, y in rows]
        n, d = len(xs), len(names)
        mean = [sum(x[j] for x in xs) / n for j in range(d)]
        std = [max(math.sqrt(sum((x[j] - mean[j]) ** 2 for x in xs) / n), 1e-6) for j in range(d)]
        zs = [[(x[j] - mean[j]) / std[j] for j in range(d)] for x in xs]
        w = [0.0] * d
        b = 0.0
        for _ in range(epochs):
            gw = [0.0] * d
            gb = 0.0
            for z, y in zip(zs, ys):
                p = _sigmoid(sum(wi * zi for wi, zi in zip(w, z)) + b)
                err = p - y
                for j in range(d):
                    gw[j] += err * z[j]
                gb += err
            for j in range(d):
                w[j] -= lr * (gw[j] / n + l2 * w[j])
            b -= lr * gb / n
        model = cls(kind, names, mean, std, w, b)
        # 训练集/验证集按 8:2 切分评估
        split = int(n * 0.8)
        scores = [model.predict_proba(dict(zip(names, z))) for z, _ in zip(zs[split:], ys[split:])]
        model.metrics = {
            "auc_valid": round(auc_score(ys[split:], scores), 4),
            "samples": n,
            "positive_rate": round(sum(ys) / n, 4),
            "epochs": epochs,
        }
        model.version = "lr-" + hashlib.sha256(
            json.dumps([round(x, 6) for x in w] + [round(b, 6)]).encode()
        ).hexdigest()[:8]
        return model

    # ---- 推理 ----
    def predict_proba(self, features: dict) -> float:
        z = self.bias + sum(
            w * ((float(features.get(n, 0.0)) - m) / s)
            for n, w, m, s in zip(self.names, self.weights, self.mean, self.std)
        )
        return _sigmoid(z)

    def explain(self, features: dict, top_k: int = 5) -> list[dict]:
        contribs = []
        for n, w, m, s in zip(self.names, self.weights, self.mean, self.std):
            value = float(features.get(n, 0.0))
            z = (value - m) / s
            contribs.append({"feature": n, "value": round(value, 3),
                             "contribution": round(w * z, 4)})
        contribs.sort(key=lambda c: abs(c["contribution"]), reverse=True)
        return contribs[:top_k]

    # ---- 持久化 ----
    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "version": self.version, "feature_names": self.names,
            "mean": self.mean, "std": self.std, "weights": self.weights, "bias": self.bias,
            "metrics": self.metrics,
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LogisticModel":
        return cls(d["kind"], d["feature_names"], d["mean"], d["std"], d["weights"],
                   d["bias"], d.get("metrics", {}), d.get("version", ""))


def model_path(kind: str) -> Path:
    return settings.model_dir / f"{kind}_model.json"


def train_and_save(kind: str, n: int = 4000) -> LogisticModel:
    model = LogisticModel.fit(kind, generate(kind, n=n))
    settings.ensure_dirs()
    model_path(kind).write_text(json.dumps(model.to_dict(), ensure_ascii=False, indent=2),
                                encoding="utf-8")
    _cache[kind] = model
    return model


def get_model(kind: str) -> LogisticModel:
    if kind in _cache:
        return _cache[kind]
    path = model_path(kind)
    if path.exists():
        model = LogisticModel.from_dict(json.loads(path.read_text(encoding="utf-8")))
    elif settings.auto_train:
        model = train_and_save(kind)
    else:
        raise RuntimeError(f"模型文件 {path} 不存在，且 CBETS_AUTO_TRAIN=0")
    _cache[kind] = model
    return model


def model_info(kind: str) -> dict:
    m = get_model(kind)
    return {"kind": kind, "version": m.version, "metrics": m.metrics,
            "feature_names": m.names, "weights": m.weights}
