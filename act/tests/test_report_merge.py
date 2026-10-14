"""Unit tests for multi-report boost↔lab merge (no network).

Loads comparison_utils by path so conftest's ID_COMPARISONS stub does not apply.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

_PATH = Path(__file__).resolve().parents[1] / "utils" / "comparison_utils.py"
_SPEC = importlib.util.spec_from_file_location("comparison_utils_real", _PATH)
_MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MOD)
_merge = _MOD.ID_COMPARISONS._merge_report_frames


def test_merge_later_source_overrides_lab_conflict():
    a = pd.DataFrame(
        [
            {"boost_id": "8080", "lab_id": "9999", "_source": "obs:43327"},
            {"boost_id": "7222", "lab_id": "1417", "_source": "obs:43327"},
        ]
    )
    b = pd.DataFrame(
        [
            {"boost_id": "8080", "lab_id": "1501", "_source": "int:53846"},
        ]
    )
    merged = _merge([a, b])
    by_boost = {str(r.boost_id): str(r.lab_id) for r in merged.itertuples()}
    assert by_boost["8080"] == "1501"
    assert by_boost["7222"] == "1417"


def test_merge_skips_empty_lab():
    a = pd.DataFrame(
        [
            {"boost_id": "9002", "lab_id": "", "_source": "int:53846"},
            {"boost_id": "8078", "lab_id": "1433", "_source": "int:53846"},
        ]
    )
    merged = _merge([a])
    assert list(merged["boost_id"]) == ["8078"]


def test_normalize_id_cell_strips_float_dot_zero():
    assert _MOD._normalize_id_cell(8078.0) == "8078"
    assert _MOD._normalize_id_cell("nan") == ""
    assert _MOD._normalize_id_cell("7178, 8066") == "7178, 8066"
