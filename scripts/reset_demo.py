"""重置演示库：删掉 data/cbets_b2c.db 及 WAL 文件，下次启动自动重建。

用法：python scripts/reset_demo.py            # 交互确认
     python scripts/reset_demo.py --yes      # 跳过确认（CI / 脚本里用）
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import settings  # noqa: E402


def main() -> None:
    db = settings.database_path
    targets = [db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")]
    existing = [p for p in targets if p.exists()]
    if not existing:
        print(f"没有需要删除的库：{db}")
        return
    print("将删除：")
    for p in existing:
        print(f"  {p} ({p.stat().st_size} bytes)")
    if "--yes" not in sys.argv:
        answer = input("输入 yes 确认删除：").strip().lower()
        if answer != "yes":
            print("已取消。")
            return
    for p in existing:
        p.unlink()
    print("已删除。下次启动 python main.py 会重建空库与演示主体。")


if __name__ == "__main__":
    main()
