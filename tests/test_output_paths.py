"""Tests cho F3 output path resolution (P4).

Yêu cầu:
- _resolve_output_root ưu tiên: --output > $SCAN2EBOOK_OUTPUT_ROOT/<slug> >
  mặc định <inbox-parent>/../output/<slug>.
- abs path in ra đầu run (regression: env unset = hành vi cũ).
"""

from __future__ import annotations

import argparse

from scan_to_ebook import cli


def _args(output=None) -> argparse.Namespace:
    return argparse.Namespace(output=output)


def test_resolve_output_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN2EBOOK_OUTPUT_ROOT", str(tmp_path / "env"))
    inbox = tmp_path / "inbox" / "mybook"
    got = cli._resolve_output_root(_args(output=tmp_path / "explicit"), inbox, "mybook")
    assert got == tmp_path / "explicit"  # --output thắng cả env


def test_resolve_output_env_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN2EBOOK_OUTPUT_ROOT", str(tmp_path / "env"))
    inbox = tmp_path / "inbox" / "mybook"
    got = cli._resolve_output_root(_args(output=None), inbox, "mybook")
    assert got == tmp_path / "env" / "mybook"  # env_root/<slug>


def test_resolve_output_default_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("SCAN2EBOOK_OUTPUT_ROOT", raising=False)
    inbox = tmp_path / "books" / "inbox" / "mybook"
    got = cli._resolve_output_root(_args(output=None), inbox, "mybook")
    # regression: hành vi cũ <inbox-parent>/../output/<slug>.
    assert got == inbox.parent.parent / "output" / "mybook"


def test_resolve_output_is_absolute_after_resolve(tmp_path, monkeypatch):
    monkeypatch.delenv("SCAN2EBOOK_OUTPUT_ROOT", raising=False)
    inbox = tmp_path / "books" / "inbox" / "mybook"
    got = cli._resolve_output_root(_args(output=None), inbox, "mybook")
    assert got.resolve().is_absolute()
