"""适配器工厂。

切换到真实环境时：
1. 新建 app/adapters/real/cbets_gateway.py 等实现类；
2. 在下面的 REGISTRY 注册；
3. 设置环境变量 CBETS_ADAPTER_MODE=real，业务层无需改动。
"""
from __future__ import annotations

from ..config import settings
from .base import CBETSAdapter, ChainAdapter, CommerceAdapter, LogisticsAdapter, WalletAdapter
from .cbets_adapter import MockCBETSAdapter
from .chain_adapter import MockChainAdapter
from .ecommerce_adapter import MockCommerceAdapter
from .logistics_adapter import MockLogisticsAdapter
from .wallet_adapter import MockWalletAdapter

REGISTRY: dict[str, dict[str, type]] = {
    "mock": {
        "commerce": MockCommerceAdapter,
        "logistics": MockLogisticsAdapter,
        "wallet": MockWalletAdapter,
        "cbets": MockCBETSAdapter,
        "chain": MockChainAdapter,
    },
}

_instances: dict[str, object] = {}


def _get(kind: str) -> object:
    mode = settings.adapter_mode
    if mode not in REGISTRY:
        raise RuntimeError(
            f"适配器模式 {mode!r} 未注册。请先在 app/adapters/__init__.py 的 REGISTRY 中注册实现类，"
            f"当前可用：{sorted(REGISTRY)}"
        )
    key = f"{mode}:{kind}"
    if key not in _instances:
        _instances[key] = REGISTRY[mode][kind]()
    return _instances[key]


def commerce() -> CommerceAdapter:
    return _get("commerce")  # type: ignore[return-value]


def logistics() -> LogisticsAdapter:
    return _get("logistics")  # type: ignore[return-value]


def wallet() -> WalletAdapter:
    return _get("wallet")  # type: ignore[return-value]


def cbets() -> CBETSAdapter:
    return _get("cbets")  # type: ignore[return-value]


def chain() -> ChainAdapter:
    return _get("chain")  # type: ignore[return-value]
