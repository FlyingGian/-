from pathlib import Path
from unittest.mock import patch
import subprocess
import bootstrap


def test_old_python_and_32bit_and_supported():
    assert "3.11" in bootstrap.runtime_error((3, 11), 64)
    assert "64位" in bootstrap.runtime_error((3, 12), 32)
    assert not bootstrap.runtime_error((3, 12), 64)
    assert not bootstrap.runtime_error((3, 13), 64)
    assert bootstrap.runtime_error((3, 14), 64)


def test_no_python_and_missing_path():
    with patch("bootstrap.subprocess.run", side_effect=FileNotFoundError):
        assert bootstrap.find_runtime() is None

    def probe(command, **kwargs):
        if command[:2] == ["py", "-3.12"]:
            return subprocess.CompletedProcess(command, 0)
        raise FileNotFoundError

    with patch("bootstrap.subprocess.run", side_effect=probe):
        assert bootstrap.find_runtime() == ["py", "-3.12"]


def test_requirements_fingerprint_includes_constraints(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    (tmp_path / "requirements.txt").write_text("\ufefftest==1\n", encoding="utf-8")
    (tmp_path / "constraints.txt").write_text("test==1\n")
    before = bootstrap.fingerprint()
    (tmp_path / "constraints.txt").write_text("test==2\n")
    assert bootstrap.fingerprint() != before


def test_start_bat_ascii():
    p = Path(bootstrap.__file__).parent
    assert (p / "start.bat").read_bytes().isascii()
    assert "data" not in (p / "uninstall.bat").read_text().split("rmdir")[1].split("\n")[0]
