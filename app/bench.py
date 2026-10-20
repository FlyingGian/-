import argparse
import asyncio
import json
import time
from pathlib import Path
import numpy as np
import httpx
from application.service import Service
from infrastructure.realtime import SinaBatchSource, TencentBatchSource


async def bench(args):
    service = Service(args.data_dir)
    data = service.repo.load()
    codes = args.codes.split(",") if args.codes else data.stocks.code.tolist()
    if not codes:
        raise ValueError(
            "没有基础证券列表，请导入stocks.csv或使用--codes；禁止用少量代码冒充全市场压测"
        )
    times = []
    coverages = []
    errors = []
    async with httpx.AsyncClient() as client:
        source = (SinaBatchSource if args.source == "sina" else TencentBatchSource)(client)
        for i in range(args.rounds):
            started = time.monotonic()
            quotes = await source.fetch(codes)
            elapsed = time.monotonic() - started
            times.append(elapsed)
            coverages.append(len({q.code for q in quotes}) / len(codes))
            errors.extend(source.errors)
            if i + 1 < args.rounds:
                await asyncio.sleep(max(1, 3 - elapsed))
    import psutil

    result = {
        "rounds": args.rounds,
        "requested_symbols": len(codes),
        "scope": (
            "全市场候选列表（仍需核验覆盖）"
            if len(codes) > 5000
            else "有限代码连接测试，非全市场压测"
        ),
        "p50_seconds": float(np.percentile(times, 50)),
        "p95_seconds": float(np.percentile(times, 95)),
        "coverage_mean": float(np.mean(coverages)),
        "failure_mean": float(1 - np.mean(coverages)),
        "rss_mb": psutil.Process().memory_info().rss / 1024**2,
        "errors": sorted(set(errors))[:10],
        "passed": bool(
            args.rounds >= 100
            and len(codes) > 5000
            and np.percentile(times, 95) <= 1
            and np.mean(coverages) >= 0.98
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=100)
    p.add_argument("--codes")
    p.add_argument("--source", choices=["sina", "tencent"], default="sina")
    p.add_argument("--data-dir")
    p.add_argument("--output")
    args = p.parse_args()
    asyncio.run(bench(args))


if __name__ == "__main__":
    main()
