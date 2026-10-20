"""Windows双击启动器。只使用标准库，失败时给中文可操作说明。"""

from pathlib import Path
import sys
import os
import struct
import subprocess
import hashlib
import time
import argparse

ROOT = Path(__file__).resolve().parent


def runtime_error(version=None, bits=None):
    version = version or sys.version_info[:2]
    bits = bits or struct.calcsize("P") * 8
    if bits != 64:
        return "需要64位Python；当前为32位。请安装Windows installer (64-bit)。"
    if not (3, 12) <= tuple(version) < (3, 14):
        return f"需要Python 3.12或3.13（64位）；当前为{version[0]}.{version[1]}。"
    return ""


def fingerprint():
    digest = hashlib.sha256()
    for name in ("requirements.txt", "constraints.txt"):
        digest.update((ROOT / name).read_text(encoding="utf-8-sig").encode("utf-8"))
    return digest.hexdigest()


def explain(message):
    print(
        "\n"
        + message
        + "\n下载地址：https://www.python.org/downloads/windows/\n安装时勾选 Add python.exe to PATH；或用Python Launcher定位已安装版本。",
        flush=True,
    )
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                message + "\nhttps://www.python.org/downloads/windows/",
                "AshareQuant 启动帮助",
                0x10,
            )
        except Exception:
            pass


def run_logged(args, log):
    with log.open("a", encoding="utf-8") as output:
        return subprocess.call(args, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT) == 0


def find_runtime():
    candidates = [["py", "-3.12"], ["py", "-3.13"], ["py", "-3"], ["python"], ["python3"]]
    if os.name == "nt":
        try:
            import winreg

            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for ver in ("3.12", "3.13"):
                    try:
                        with winreg.OpenKey(
                            hive, rf"SOFTWARE\Python\PythonCore\{ver}\InstallPath"
                        ) as key:
                            path = winreg.QueryValueEx(key, "")[0]
                            candidates.append([str(Path(path) / "python.exe")])
                    except OSError:
                        pass
        except ImportError:
            pass
    for command in candidates:
        try:
            test = subprocess.run(
                command
                + [
                    "-c",
                    'import sys,struct;raise SystemExit(not ((3,12)<=sys.version_info[:2]<(3,14) and struct.calcsize("P")==8))',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            if test.returncode == 0:
                return command
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def bootstrap(args):
    error = runtime_error()
    if error:
        explain(error)
        return 2
    env = ROOT / ".venv"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    log = logdir / "bootstrap.log"
    if python.exists():
        if not run_logged(
            [
                str(python),
                "-c",
                'import sys,struct;raise SystemExit(not ((3,12)<=sys.version_info[:2]<(3,14) and struct.calcsize("P")==8))',
            ],
            log,
        ):
            print("已有虚拟环境版本不兼容，将保留为备份后重建。", flush=True)
            backup = ROOT / f".venv-backup-{int(time.time())}"
            env.rename(backup)
    if not python.exists():
        print("[1/3] 正在创建隔离环境…", flush=True)
        if not run_logged([sys.executable, "-m", "venv", str(env)], log):
            if not run_logged(
                [sys.executable, "-m", "venv", "--without-pip", str(env)], log
            ) or not run_logged([str(python), "-m", "ensurepip", "--upgrade"], log):
                explain(f"创建虚拟环境失败。请使用官方完整版Python，并检查目录权限。日志：{log}")
                return 3
    marker = env / ".deps_hash"
    expected = fingerprint()
    if args.repair or not marker.exists() or marker.read_text().strip() != expected:
        print(
            "[2/3] 依赖首次安装或版本变更，正在安装预编译包。详细进度见 logs/bootstrap.log。",
            flush=True,
        )
        base = [
            str(python),
            "-m",
            "pip",
            "install",
            "--only-binary=:all:",
            "--disable-pip-version-check",
            "--timeout",
            "20",
            "--retries",
            "1",
            "-r",
            "requirements.txt",
            "-c",
            "constraints.txt",
        ]
        commands = []
        if any((ROOT / "wheels").glob("*.whl")):
            commands.append(base + ["--no-index", "--find-links", str(ROOT / "wheels")])
        if not args.offline:
            commands.extend(
                [
                    base + ["--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                    base + ["--index-url", "https://pypi.org/simple"],
                ]
            )
        success = False
        for command in commands:
            print("安装源：" + command[-1], flush=True)
            if run_logged(command, log):
                success = True
                break
        if not success:
            explain(
                "依赖安装失败。首次安装需要联网，或使用完整wheels离线包。\n排障：.venv\\Scripts\\python.exe -m pip install --only-binary=:all: -r requirements.txt -c constraints.txt\n日志："
                + str(log)
            )
            return 4
        if not run_logged([str(python), "-m", "pip", "check"], log):
            explain("依赖版本检查失败，请运行start.bat --repair。")
            return 5
        marker.write_text(expected, encoding="utf-8")
    else:
        print("[2/3] 依赖未变化，跳过安装。", flush=True)
    if args.check_only:
        return 0
    print("[3/3] 启动AshareQuant…", flush=True)
    gui = python.with_name("pythonw.exe") if os.name == "nt" and not args.debug else python
    command = [str(gui), "-m", "app"]
    with (logdir / "gui.log").open("a", encoding="utf-8") as output:
        if args.debug:
            return subprocess.call(command, cwd=ROOT)
        subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--find", action="store_true")
    args = parser.parse_args()
    if args.find:
        selected = find_runtime()
        if selected:
            return subprocess.call(
                selected
                + [str(ROOT / "bootstrap.py")]
                + [x for x in sys.argv[1:] if x != "--find"],
                cwd=ROOT,
            )
        explain(
            "没有找到受支持的64位Python 3.12/3.13。若已安装但未加入PATH，请安装Python Launcher，或修复安装并勾选Add to PATH。"
        )
        return 2
    try:
        return bootstrap(args)
    except Exception as exc:
        explain("启动失败：" + str(exc) + "\n请将目录解压到可写路径，避免直接在压缩包内运行。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
