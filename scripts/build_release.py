"""构建源码与Windows离线包，附Git历史和逐文件校验。"""

from pathlib import Path
import hashlib
import json
import subprocess
import zipfile
import argparse

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "data",
    "logs",
    "ci-data",
}


def sources():
    return [
        p
        for p in sorted(ROOT.rglob("*"))
        if p.is_file()
        and not (set(p.relative_to(ROOT).parts) & EXCLUDED)
        and p.suffix not in (".whl", ".pyc", ".bundle")
        and p.name not in (".coverage", "SOURCE_MANIFEST.json")
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT.parent / "deliverables"))
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    bundle = output / "AshareQuant.git.bundle"
    subprocess.check_call(["git", "bundle", "create", str(bundle), "--all"], cwd=ROOT)
    entries = sources()
    manifest = {
        "version": "1.0.0",
        "git_commit": commit,
        "files": [
            {
                "path": str(p.relative_to(ROOT)),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "bytes": p.stat().st_size,
            }
            for p in entries
        ],
    }
    source = output / "AshareQuant-v1.0.0-source.zip"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in entries:
            archive.write(path, Path("AshareQuant") / path.relative_to(ROOT))
        archive.write(bundle, "AshareQuant/AshareQuant.git.bundle")
        archive.writestr(
            "AshareQuant/SOURCE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    wheels = output / "AshareQuant-Windows-Wheels.zip"
    with zipfile.ZipFile(wheels, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted((ROOT / "wheels").iterdir()):
            if path.is_file():
                archive.write(path, Path("AshareQuant/wheels") / path.name)
    hashes = []
    for path in (source, wheels, bundle):
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                assert archive.testzip() is None
        else:
            subprocess.check_call(["git", "bundle", "verify", str(path)], cwd=ROOT)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes.append(f"{digest}  {path.name}")
        print(f"{path.name}: {path.stat().st_size / 1024**2:.2f} MiB, SHA256 {digest}")
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
