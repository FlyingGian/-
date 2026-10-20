"""本地仓库：SQLite 元数据、分年 Parquet 日线、可验证快照。"""

from pathlib import Path
from typing import Protocol
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
import os
import pandas as pd
from domain.data import ResearchData

TABLE_KEYS = {
    "stocks": ["code"],
    "calendar": ["date"],
    "states": ["code", "date"],
    "financials": ["code", "report_period", "announce_date", "version"],
    "valuations": ["code", "date"],
    "members": ["index_code", "code", "start_date"],
    "actions": ["code", "ex_date"],
    "benchmarks": ["code", "date"],
}
REQUIRED = {
    "daily": [
        "code",
        "date",
        "open",
        "high",
        "low",
        "close",
        "prev_close",
        "volume",
        "amount",
        "adj_factor",
    ],
    "stocks": ["code", "name", "board", "list_date"],
    "calendar": ["date", "is_open"],
    "states": ["code", "date", "is_st", "industry", "delisting"],
    "financials": ["code", "report_period", "announce_date", "version"],
    "valuations": ["code", "date"],
    "members": ["index_code", "code", "start_date", "end_date"],
    "actions": ["code", "ex_date", "cash_per_share", "share_ratio", "rights_ratio", "rights_price"],
    "benchmarks": ["code", "date", "close"],
}
BOOLEAN_FIELDS = {"is_open", "is_st", "delisting", "suspended"}
DATE_FIELDS = {
    "date",
    "list_date",
    "delist_date",
    "report_period",
    "announce_date",
    "available_date",
    "start_date",
    "end_date",
    "ex_date",
}
TEXT_FIELDS = {"code", "index_code", "name", "board", "industry", "source"} | DATE_FIELDS


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    for c in f:
        if c in ("code", "index_code"):
            f[c] = f[c].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        elif c in DATE_FIELDS:
            values = pd.to_datetime(f[c].replace("", None).astype("string"), errors="coerce")
            invalid = f[c].notna() & f[c].astype(str).str.strip().ne("") & values.isna()
            if invalid.any():
                raise ValueError(f"{c} 包含无效日期")
            f[c] = values.dt.strftime("%Y-%m-%d")
        elif c in BOOLEAN_FIELDS:
            mapping = {
                "true": True,
                "false": False,
                "1": True,
                "0": False,
                "1.0": True,
                "0.0": False,
            }
            converted = f[c].astype(str).str.lower().map(mapping)
            if converted.isna().any():
                raise ValueError(f"{c} 只能填 true/false 或 1/0")
            f[c] = converted.astype(bool)
        elif c not in TEXT_FIELDS:
            f[c] = pd.to_numeric(f[c], errors="raise")
    return f


def validate_data(kind: str, frame: pd.DataFrame) -> list[dict]:
    issues = []
    absent = set(REQUIRED.get(kind, [])) - set(frame)
    if absent:
        return [{"level": "error", "message": f"缺少字段 {sorted(absent)}"}]
    if frame.empty:
        return [{"level": "warning", "message": "数据为空"}]
    key = ["code", "date"] if kind == "daily" else TABLE_KEYS[kind]
    if frame.duplicated(key).any():
        issues.append({"level": "error", "message": "存在重复主键"})
    if frame[key].isna().any().any() and kind != "financials":
        issues.append({"level": "error", "message": "主键为空"})
    if "code" in frame and not frame.code.str.fullmatch(r"\d{6}").all():
        issues.append({"level": "error", "message": "代码须为六位数字"})
    if kind == "daily":
        if (frame[["open", "high", "low", "close", "prev_close"]] <= 0).any().any():
            issues.append({"level": "error", "message": "价格必须为正"})
        if (
            frame[["open", "high", "low", "close", "prev_close", "volume", "amount"]]
            .isna()
            .any()
            .any()
        ):
            issues.append({"level": "error", "message": "行情关键字段缺失"})
        if ((frame.volume < 0) | (frame.amount < 0)).any():
            issues.append({"level": "error", "message": "成交量额不得为负"})
        if ((frame.volume == 0) & (frame.amount > 0)).any():
            issues.append({"level": "error", "message": "零成交量却有成交额"})
        if (
            (frame.high < frame[["open", "close", "low"]].max(axis=1))
            | (frame.low > frame[["open", "close", "high"]].min(axis=1))
        ).any():
            issues.append({"level": "error", "message": "OHLC 关系错误"})
        if (frame.adj_factor <= 0).any():
            issues.append({"level": "error", "message": "复权因子无效"})
        if frame.adj_factor.isna().any():
            issues.append({"level": "warning", "message": "缺少复权因子；严格回测不可用"})
        if frame.adj_factor.eq(1).all():
            issues.append(
                {"level": "warning", "message": "复权因子全1，请核实数据源，不自动视为正确"}
            )
        if (frame.close / frame.prev_close - 1).abs().gt(0.45).any():
            issues.append({"level": "warning", "message": "涨跌幅超过45%，需核验新股/除权/规则"})
    elif kind == "financials":
        value_cols = [c for c in frame if c not in TEXT_FIELDS and c != "version"]
        if not value_cols or frame[value_cols].isna().all().all():
            issues.append({"level": "warning", "message": "财务数值字段全空"})
        if frame.announce_date.isna().any():
            issues.append(
                {"level": "warning", "message": "公告日缺失将使用保守法定日期，存在延迟披露风险"}
            )
    elif kind == "stocks":
        if not frame.board.isin(["main", "chinext", "star", "bse"]).all():
            issues.append({"level": "error", "message": "未知板块编码"})
    return issues


class Repository(Protocol):
    def load(self) -> ResearchData: ...
    def ingest(self, kind: str, frame: pd.DataFrame, source: str = "csv") -> list[dict]: ...


class LocalRepository:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if self.root.name == "realtime":
            raise ValueError("日频仓库不能指向实时目录")
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "research.sqlite"
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY,time TEXT,kind TEXT,detail TEXT)"
            )

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def audit(self, kind: str, detail: str):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO audit(time,kind,detail) VALUES(?,?,?)",
                (datetime.now(timezone.utc).isoformat(), kind, detail),
            )

    def ingest(self, kind: str, frame: pd.DataFrame, source: str = "csv") -> list[dict]:
        if kind not in REQUIRED:
            raise ValueError("未知数据表")
        frame = normalize(frame)
        issues = validate_data(kind, frame)
        fatal = [i["message"] for i in issues if i["level"] == "error"]
        if fatal:
            raise ValueError("；".join(fatal))
        if frame.empty:
            return issues
        if kind == "daily":
            for year, g in frame.groupby(frame.date.str[:4]):
                path = self.root / "daily" / f"year={year}" / "bars.parquet"
                path.parent.mkdir(parents=True, exist_ok=True)
                previous = pd.read_parquet(path) if path.exists() else pd.DataFrame()
                merged = (
                    pd.concat([previous, g], ignore_index=True)
                    .drop_duplicates(["code", "date"], keep="last")
                    .sort_values(["code", "date"])
                )
                tmp = path.with_suffix(".tmp")
                merged.to_parquet(tmp, index=False, compression="snappy")
                os.replace(tmp, path)
        else:
            with self.connection() as conn:
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (kind,)
                ).fetchone()
                old = (
                    pd.read_sql_query(f'SELECT * FROM "{kind}"', conn) if exists else pd.DataFrame()
                )
                merged = pd.concat([old, frame], ignore_index=True)
                keys = TABLE_KEYS[kind]
                # 财务历史版本不可覆写；同版本不同内容必须提供新公告日期/版本。
                if kind == "financials" and not old.empty:
                    collision = old.merge(frame, on=keys, suffixes=("_old", "_new"))
                    for c in set(old) & set(frame) - set(keys):
                        a, b = collision.get(c + "_old"), collision.get(c + "_new")
                        if (
                            a is not None
                            and b is not None
                            and ((a != b) & ~(a.isna() & b.isna())).any()
                        ):
                            raise ValueError("财务历史版本禁止覆写，请递增version并使用更正公告日")
                merged = merged.drop_duplicates(keys, keep="last")
                # staging 表替换，显式事务；不让 pandas replace 覆盖生产表。
                staging = f"_stage_{kind}"
                merged.to_sql(staging, conn, if_exists="replace", index=False)
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(f'DROP TABLE IF EXISTS "{kind}"')
                conn.execute(f'ALTER TABLE "{staging}" RENAME TO "{kind}"')
                columns = ",".join(f'"{k}"' for k in keys)
                conn.execute(f'CREATE UNIQUE INDEX "idx_{kind}" ON "{kind}" ({columns})')
        self.audit(
            "ingest",
            json.dumps(
                {"kind": kind, "rows": len(frame), "source": source, "issues": issues},
                ensure_ascii=False,
            ),
        )
        return issues

    def load(self) -> ResearchData:
        parts = sorted((self.root / "daily").glob("year=*/bars.parquet"))
        daily = (
            pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
            if parts
            else pd.DataFrame(columns=REQUIRED["daily"])
        )
        frames = {}
        with self.connection() as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for kind in TABLE_KEYS:
                frames[kind] = (
                    normalize(pd.read_sql_query(f'SELECT * FROM "{kind}"', conn))
                    if kind in tables
                    else pd.DataFrame(columns=REQUIRED[kind])
                )
        digest = hashlib.sha256()
        for kind, frame in {"daily": daily, **frames}.items():
            keys = ["code", "date"] if kind == "daily" else TABLE_KEYS[kind]
            ordered = frame.sort_values(keys).reindex(sorted(frame.columns), axis=1)
            digest.update(kind.encode())
            digest.update("|".join(ordered.columns).encode())
            digest.update(pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes())
        return ResearchData(daily=daily, version=digest.hexdigest(), **frames)

    def version(self) -> str:
        # 版本由实际加载的数据内容生成，避免加载完成后另扫文件引入新旧哈希错配。
        return self.load().version

    def get_price(self, code: str, start: str, end: str, adjust: str = "hfq") -> pd.DataFrame:
        if adjust not in ("none", "hfq", "qfq"):
            raise ValueError("未知复权方式")
        f = self.load().daily
        f = f.loc[(f.code == code) & (f.date >= start) & (f.date <= end)].sort_values("date").copy()
        if f.empty or adjust == "none":
            return f
        scale = f.adj_factor if adjust == "hfq" else f.adj_factor / f.adj_factor.iloc[-1]
        for c in ("open", "high", "low", "close"):
            f[c] *= scale
        return f

    def query(self, sql: str) -> pd.DataFrame:
        import duckdb

        if not sql.lstrip().lower().startswith("select"):
            raise ValueError("只允许SELECT")
        data = self.load()
        conn = duckdb.connect(":memory:")
        try:
            conn.register("daily", data.daily)
            return conn.execute(sql).fetchdf()
        finally:
            conn.close()
