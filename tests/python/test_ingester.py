"""Tests for ingestion command failure signaling."""
from __future__ import annotations

from freecreds import ingester


def test_all_targets_returns_failure_when_any_target_failed(monkeypatch):
    monkeypatch.setattr(
        ingester,
        "ingest_all_targets",
        lambda *_args, **_kwargs: {"succeeded": ["CSUFULL"], "failed": ["UCB"]},
    )

    assert ingester.main(["--all"]) == 1


def test_all_targets_returns_success_when_every_target_succeeded(monkeypatch):
    monkeypatch.setattr(
        ingester,
        "ingest_all_targets",
        lambda *_args, **_kwargs: {"succeeded": ["CSUFULL"], "failed": []},
    )

    assert ingester.main(["--all"]) == 0
