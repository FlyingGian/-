from pathlib import Path
from importlib.metadata import version, PackageNotFoundError
import shutil
import sys
import struct
import tempfile
import httpx


def check_environment(data_dir: Path, network: bool = False) -> list[dict]:
    rows = []

    def add(name, ok, detail, fix=""):
        rows.append(
            {"项目": name, "状态": "通过" if ok else "需处理", "详情": str(detail), "处理方法": fix}
        )

    ok = (3, 12) <= sys.version_info[:2] < (3, 14)
    add(
        "Python版本",
        ok,
        sys.version.split()[0],
        "安装64位CPython 3.12或3.13：https://www.python.org/downloads/windows/",
    )
    add(
        "64位",
        struct.calcsize("P") == 8,
        struct.calcsize("P") * 8,
        "安装Windows installer (64-bit)",
    )
    add("虚拟环境", sys.prefix != sys.base_prefix, sys.prefix, "用start.bat启动以自动创建隔离环境")
    for name in (
        "PySide6",
        "pandas",
        "numpy",
        "pyarrow",
        "httpx",
        "openpyxl",
        "scikit-learn",
        "duckdb",
    ):
        try:
            add(name, True, version(name))
        except PackageNotFoundError:
            add(name, False, "未安装", "重新运行start.bat --repair自动修复")
    free = shutil.disk_usage(data_dir).free
    add(
        "磁盘空间",
        free > 2 * 1024**3,
        f"{free/1024**3:.1f} GB可用",
        "释放至少2GB；全市场历史建议20GB以上",
    )
    try:
        with tempfile.TemporaryFile(dir=data_dir) as f:
            f.write(b"ok")
        add("数据目录可写", True, data_dir)
    except OSError:
        add("数据目录可写", False, data_dir, "移动到有写入权限的本地目录")
    if network:
        for name, url in (
            ("新浪行情", "https://hq.sinajs.cn/list=sh000001"),
            ("腾讯行情", "https://qt.gtimg.cn/q=sh000001"),
        ):
            try:
                r = httpx.get(url, headers={"Referer": "https://finance.sina.com.cn/"}, timeout=5)
                add(
                    name,
                    r.status_code == 200 and len(r.content) > 30,
                    f"HTTP {r.status_code}",
                    "检查网络、代理与来源访问权限；离线研究仍可使用",
                )
            except httpx.HTTPError as exc:
                add(name, False, type(exc).__name__, "检查网络/代理，稍后重试")
    return rows
