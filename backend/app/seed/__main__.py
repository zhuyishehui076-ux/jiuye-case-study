"""种子 CLI：`python -m app.seed --demo [--reset]`（SPEC §7 T1）。

连接 settings.database_url（生产/CI 为真实 PostgreSQL）。生成后打印各表计数。
"""

from __future__ import annotations

import argparse
import sys

from app.db.session import SessionLocal
from app.seed.demo import SeedExists, run_demo_seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.seed", description="演示种子数据生成器")
    parser.add_argument("--demo", action="store_true", help="生成演示种子数据")
    parser.add_argument("--reset", action="store_true", help="先 TRUNCATE 业务表再重建")
    args = parser.parse_args(argv)

    if not args.demo:
        parser.error("请指定 --demo")

    with SessionLocal() as db:
        try:
            counts = run_demo_seed(db, reset=args.reset)
        except SeedExists as e:
            print(f"[seed] 拒绝执行：{e}", file=sys.stderr)
            return 2

    print("[seed] 演示数据生成完成，各表计数：")
    width = max(len(k) for k in counts)
    for key, val in counts.items():
        print(f"  {key.ljust(width)} : {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
