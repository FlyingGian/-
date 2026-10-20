"""在联网机器生成两个Windows版本可用的离线依赖。零本地编译。"""

from pathlib import Path
import subprocess
import sys
import argparse
import json
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--python", choices=["312", "313", "both"], default="both")
    p.add_argument("--resolve", action="store_true")
    args = p.parse_args()
    target = ROOT / "wheels"
    target.mkdir(exist_ok=True)
    for py in ["312", "313"] if args.python == "both" else [args.python]:
        command = [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--platform",
            "win_amd64",
            "--python-version",
            py,
            "--implementation",
            "cp",
            "--abi",
            "cp" + py,
            "-r",
            str(ROOT / "requirements.txt"),
            "-d",
            str(target),
        ]
        if not args.resolve:
            command += ["-c", str(ROOT / "constraints.txt")]
        subprocess.check_call(command)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--no-deps",
            "pywin32-ctypes==0.2.3",
            "colorama==0.4.6",
            "-d",
            str(target),
        ]
    )
    records = []
    constraints = {}
    for file in sorted(target.glob("*.whl")):
        with zipfile.ZipFile(file) as z:
            metadata = [x for x in z.namelist() if x.endswith(".dist-info/METADATA")][0]
            text = z.read(metadata).decode()
            name = next(x[6:] for x in text.splitlines() if x.startswith("Name: "))
            version = next(x[9:] for x in text.splitlines() if x.startswith("Version: "))
            if name in constraints and constraints[name] != version:
                raise RuntimeError(f"锁版本冲突：{name}")
            constraints[name] = version
        records.append(
            {
                "file": file.name,
                "bytes": file.stat().st_size,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        )
    if args.resolve:
        (ROOT / "constraints.txt").write_text(
            "# Windows CPython 3.12/3.13 全量锁，由wheel元数据生成\n"
            + "\n".join(
                f"{k}=={v}" for k, v in sorted(constraints.items(), key=lambda x: x[0].lower())
            )
            + "\n",
            encoding="utf-8",
        )
    (target / "manifest.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(
        f"完成 {len(records)} 个wheel；manifest.json包含SHA256；请把wheels目录与源码一起复制到离线电脑。"
    )


if __name__ == "__main__":
    main()
