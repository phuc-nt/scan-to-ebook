"""Tests cho `scan2ebook doctor` (P2): env self-check.

Yêu cầu cứng:
- Exit 0 iff tất cả check ESSENTIAL pass (rclone vắng KHÔNG đổi exit code).
- Key value KHÔNG BAO GIỜ lọt ra output (chỉ present/absent).
- KHÔNG gọi ocr.require_api_key (raise SystemExit) — check os.environ trực tiếp.
"""

from __future__ import annotations

import argparse
import json

from scan_to_ebook import cli, doctor


# ----------------------------------------------------------- run_checks logic

def test_run_checks_all_pass(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which",
                        lambda x: f"/usr/bin/{x}")  # pandoc + rclone present
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pandoc 3.1.2\n"})())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    results = doctor.run_checks()
    by = {c["name"]: c for c in results}
    assert by["python"]["ok"] and by["python"]["essential"]
    assert by["pandoc"]["ok"] and by["pandoc"]["detail"] == "pandoc 3.1.2"
    assert by["openrouter_key"]["ok"]
    assert by["rclone"]["ok"] and not by["rclone"]["essential"]
    assert doctor.all_essential_ok(results)


def test_run_checks_pandoc_missing_fails_essential(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    results = doctor.run_checks()
    by = {c["name"]: c for c in results}
    assert not by["pandoc"]["ok"]
    assert not doctor.all_essential_ok(results)


def test_run_checks_key_missing_fails(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pandoc 3\n"})())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    results = doctor.run_checks()
    assert not doctor.all_essential_ok(results)


def test_rclone_absent_does_not_fail(monkeypatch):
    """rclone vắng = non-essential → all_essential_ok vẫn True nếu còn lại pass."""
    def which(x):
        return None if x == "rclone" else f"/usr/bin/{x}"

    monkeypatch.setattr(doctor.shutil, "which", which)
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pandoc 3\n"})())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    results = doctor.run_checks()
    by = {c["name"]: c for c in results}
    assert not by["rclone"]["ok"] and not by["rclone"]["essential"]
    assert doctor.all_essential_ok(results)  # rclone vắng không kéo fail


def test_pandoc_version_subprocess_error_falls_back(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: f"/usr/bin/{x}")

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(doctor.subprocess, "run", boom)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    by = {c["name"]: c for c in doctor.run_checks()}
    assert by["pandoc"]["ok"]  # which thấy → ok, version fallback
    assert "version unknown" in by["pandoc"]["detail"]


# ----------------------------------------------------------- exit codes

def test_cmd_doctor_exit0_when_essential_ok(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pandoc 3\n"})())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    assert cli.cmd_doctor(argparse.Namespace(json=False)) == 0


def test_cmd_doctor_exit1_when_pandoc_missing(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    assert cli.cmd_doctor(argparse.Namespace(json=False)) == 1


# ----------------------------------------------------------- json mode

def test_cmd_doctor_json_parseable(monkeypatch, capsys):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pandoc 3\n"})())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    rc = cli.cmd_doctor(argparse.Namespace(json=True))
    assert rc == 0
    obj = json.loads(capsys.readouterr().out.strip())
    assert obj["status"] == "ok"
    assert {c["name"] for c in obj["checks"]} == {
        "python", "pandoc", "openrouter_key", "heic_convert", "pdf_render",
        "rar_backend", "rclone"}


def test_cmd_doctor_json_status_fail(monkeypatch, capsys):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    rc = cli.cmd_doctor(argparse.Namespace(json=True))
    assert rc == 1
    assert json.loads(capsys.readouterr().out.strip())["status"] == "fail"


# ----------------------------------------------------------- SECURITY: no key leak

def test_key_value_never_in_output_human(monkeypatch, capsys):
    secret = "sk-secret-xyz-do-not-leak"
    monkeypatch.setattr(doctor.shutil, "which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pandoc 3\n"})())
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    cli.cmd_doctor(argparse.Namespace(json=False))
    cap = capsys.readouterr()
    assert secret not in cap.out and secret not in cap.err


def test_key_value_never_in_output_json(monkeypatch, capsys):
    secret = "sk-secret-xyz-do-not-leak"
    monkeypatch.setattr(doctor.shutil, "which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pandoc 3\n"})())
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    cli.cmd_doctor(argparse.Namespace(json=True))
    cap = capsys.readouterr()
    assert secret not in cap.out and secret not in cap.err
