"""全局配置：全部走环境变量，默认值只在本地开发用。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Settings:
    # 存储
    data_dir: Path = _path("CBETS_DATA_DIR", BASE_DIR / "data")
    database_path: Path = _path("CBETS_DB_PATH", _path("CBETS_DATA_DIR", BASE_DIR / "data") / "cbets_b2c.db")
    model_dir: Path = _path("CBETS_MODEL_DIR", _path("CBETS_DATA_DIR", BASE_DIR / "data") / "models")

    # 资金口径
    platform_fee_bps: int = _int("CBETS_PLATFORM_FEE_BPS", 60)   # 0.60%，按分计算，向下取整
    escrow_hold_hours: int = _int("CBETS_ESCROW_HOLD_HOURS", 72)
    review_hold_hours: int = _int("CBETS_REVIEW_HOLD_HOURS", 168)  # 人工复核场景延长冻结 7 天

    # 风险阈值
    block_threshold: float = _float("CBETS_BLOCK_THRESHOLD", 0.75)
    review_threshold: float = _float("CBETS_REVIEW_THRESHOLD", 0.45)

    # 适配器
    adapter_mode: str = os.getenv("CBETS_ADAPTER_MODE", "mock")
    cbets_base_url: str = os.getenv("CBETS_BASE_URL", "")
    cbets_app_id: str = os.getenv("CBETS_APP_ID", "")
    cbets_app_secret: str = os.getenv("CBETS_APP_SECRET", "")
    chain_mode: str = os.getenv("CBETS_CHAIN_MODE", "mock")

    # 运维
    admin_key: str = os.getenv("CBETS_ADMIN_KEY", "")
    cors_origins: str = os.getenv("CBETS_CORS_ORIGINS", "*")
    auto_train: bool = os.getenv("CBETS_AUTO_TRAIN", "1") == "1"
    log_level: str = os.getenv("CBETS_LOG_LEVEL", "INFO")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
