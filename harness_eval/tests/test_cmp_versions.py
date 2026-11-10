"""Tests for scripts/cmp_versions.py on tiny fake run directories."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cmp_versions as cv  # noqa: E402


def _cp(run: Path, problem: str, cp: int, passed: dict, total: dict, cost: float | None = 1.0) -> None:
    d = run / problem / f"checkpoint_{cp}"
    d.mkdir(parents=True)
    (d / "evaluation.json").write_text(json.dumps({"pass_counts": passed, "total_counts": total}))
    if cost is not None:
        (d / "inference_result.json").write_text(json.dumps({"usage": {"cost": cost}}))


ALL = {"Core": 2, "Functionality": 2, "Error": 1, "Regression": 1}


def _fixture(tmp: Path) -> dict[str, list[str]]:
    # arm a: target recli cp1-2 in r1; held-out meshctl in r2 (and a stray recli in r2, ignored)
    _cp(tmp / "a1", "recli", 1, ALL, ALL, 2.0)
    _cp(tmp / "a1", "recli", 2, {**ALL, "Regression": 0}, ALL, 3.0)  # iso, not strict
    _cp(tmp / "a2", "meshctl", 1, {**ALL, "Core": 1}, ALL, 0.5)  # not core
    _cp(tmp / "a2", "recli", 3, ALL, ALL, 9.0)
    _cp(tmp / "a1", "not_a_comparison_problem", 1, ALL, ALL, 99.0)
    # arm b: recli cp1 only (cp2 has no evaluation.json), meshctl cp1, no cost file
    _cp(tmp / "b", "recli", 1, {**ALL, "Functionality": 1}, ALL, None)  # core, not iso
    (tmp / "b" / "recli" / "checkpoint_2").mkdir()
    _cp(tmp / "b", "meshctl", 1, ALL, ALL, 1.0)
    return {"a": ["a1", "a2"], "b": ["b"], "empty": ["does-not-exist"]}


def test_merge_common_and_available(tmp_path: Path):
    rep = cv.compare(_fixture(tmp_path), tmp_path)
    assert rep["compared"] == ["a", "b"]
    assert rep["no_data"] == ["empty"]
    assert rep["common_checkpoints"] == [("meshctl", "checkpoint_1"), ("recli", "checkpoint_1")]

    avail_a = rep["views"]["available"]["all"]["a"]
    # later dir does not add recli/checkpoint_3: recli came from a1 already
    assert avail_a["checkpoints"] == 3
    assert (avail_a["core"], avail_a["iso"], avail_a["strict"]) == (2, 2, 1)
    assert (avail_a["passed"], avail_a["total"]) == (6 + 5 + 5, 18)
    assert avail_a["cost"] == 5.5

    common = rep["views"]["common"]
    assert common["targets"]["a"]["checkpoints"] == 1
    assert common["targets"]["a"]["cost"] == 2.0
    assert common["held-out"]["a"]["core"] == 0
    b = common["all"]["b"]
    assert (b["checkpoints"], b["core"], b["iso"], b["strict"]) == (2, 2, 1, 1)
    assert b["cost"] == 1.0  # missing inference_result counts as 0
    assert abs(b["pct"] - 11 / 12) < 1e-9

    pp = rep["per_problem"]
    assert pp["recli"]["a"] == {
        "checkpoints": 2, "passed": 11, "total": 12, "common_passed": 6, "common_total": 6,
    }
    assert pp["file_backup"]["b"]["checkpoints"] == 0


def test_cli_prints_tables_and_writes_json(tmp_path: Path, capsys, monkeypatch):
    arms = _fixture(tmp_path)
    monkeypatch.setattr(cv, "ARM_DIRS", {"a": arms["a"]})
    out = tmp_path / "rep.json"
    rc = cv.main(["--results", str(tmp_path), "--v7", str(tmp_path / "b"), "--json", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "== common checkpoints" in text and "== available checkpoints" in text
    assert "common checkpoints: 2 across 2 problems (meshctl, recli)" in text
    assert "11/12 [2]" in text  # per-problem cell for arm a / recli
    rep = json.loads(out.read_text())
    assert rep["compared"] == ["a", "v7"]
    assert rep["arms"]["v7"] == [str(tmp_path / "b")]


def test_no_data_exits_nonzero(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cv, "ARM_DIRS", {"x": ["nothing"]})
    assert cv.main(["--results", str(tmp_path)]) == 1


def test_arms_pairs_and_group_warning(tmp_path: Path, capsys, monkeypatch):
    arms = _fixture(tmp_path)
    # c has only held-out meshctl, so the all-arm common set has no targets.
    _cp(tmp_path / "c", "meshctl", 1, ALL, ALL, 1.0)
    monkeypatch.setattr(cv, "ARM_DIRS", {"a": arms["a"], "c": ["c"]})
    rc = cv.main(["--results", str(tmp_path), "--v7", str(tmp_path / "b")])
    assert rc == 0
    text = capsys.readouterr().out
    assert "warning: c has no targets checkpoints" in text
    assert "common checkpoints: 1 across 1 problems (meshctl)" in text
    assert "== pairs: v7 against each arm" in text
    rep = cv.compare({"a": arms["a"], "c": ["c"], "v7": ["b"]}, tmp_path, focus="v7")
    assert rep["pairs"]["a"]["targets"]["v7"]["checkpoints"] == 1  # recli cp1, shared by v7 and a only
    assert rep["pairs"]["c"]["targets"]["v7"]["checkpoints"] == 0
    # --arms limits the comparison, and the common set, to the named arms.
    assert cv.main(["--results", str(tmp_path), "--v7", str(tmp_path / "b"), "--arms", "a,v7"]) == 0
    assert "common checkpoints: 2 across 2 problems (meshctl, recli)" in capsys.readouterr().out
    import pytest

    with pytest.raises(SystemExit):
        cv.main(["--results", str(tmp_path), "--arms", "a,nosuch"])


def test_relative_v7_path_and_missing_v7_data(tmp_path: Path, capsys, monkeypatch):
    arms = _fixture(tmp_path)
    monkeypatch.setattr(cv, "ARM_DIRS", {"a": arms["a"]})
    monkeypatch.chdir(tmp_path.parent)
    rel = f"{tmp_path.name}/b"  # relative to the current folder, not to --results
    assert cv.main(["--results", str(tmp_path), "--v7", rel]) == 0
    assert "no data (left out): v7" not in capsys.readouterr().out
    assert "v7" in cv.compare({"a": arms["a"], "v7": [rel]}, tmp_path)["compared"]
    capsys.readouterr()
    assert cv.main(["--results", str(tmp_path), "--v7", "no-such-run"]) == 1
    assert "--v7 no-such-run: no checkpoint with evaluation.json found" in capsys.readouterr().err


def test_warns_when_smoke_checkpoints_differ(tmp_path: Path):
    arms = _fixture(tmp_path)
    (tmp_path / "a1" / "manifest.json").write_text(json.dumps({"smoke_checkpoints": 8}))
    (tmp_path / "b" / "manifest.json").write_text(json.dumps({"smoke_checkpoints": 2}))
    rep = cv.compare({"a": arms["a"], "v7": ["b"]}, tmp_path)
    assert any("smoke_checkpoints differ between arms (a=8, v7=2)" in w for w in rep["warnings"])
    (tmp_path / "b" / "manifest.json").write_text(json.dumps({"smoke_checkpoints": 8}))
    assert not any("smoke_checkpoints" in w for w in cv.compare({"a": arms["a"], "v7": ["b"]}, tmp_path)["warnings"])
