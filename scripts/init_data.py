"""初始化本地数据，未配置来源时给明确指引。"""

import sys
import argparse
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application.service import Service


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="today")
    p.add_argument("--csv-dir")
    p.add_argument("--source", choices=["eastmoney", "tushare"])
    p.add_argument("--data-dir")
    args = p.parse_args()
    from datetime import datetime
    from zoneinfo import ZoneInfo

    end = (
        datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        if args.end == "today"
        else args.end
    )
    s = Service(args.data_dir)
    start = time.monotonic()
    if args.csv_dir:
        files = sorted(Path(args.csv_dir).glob("*.csv"))
        s.import_csv([str(f) for f in files], lambda n, m: print(n, m))
    else:
        if args.source:
            s.save_settings({"source": args.source})
        s.update(args.start, end, lambda n, m: print(n, m))
    report = s.coverage()
    report["elapsed_seconds"] = time.monotonic() - start
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
