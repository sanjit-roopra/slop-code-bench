"""Tests for the smoke catalog builder, manifest writer and run.sh argument handling."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import make_smoke_problem  # noqa: E402
import manifest  # noqa: E402


def _make_problem(root: Path, n: int) -> Path:
    prob = root / "toy"
    (prob / "tests" / "data").mkdir(parents=True)
    (prob / "solutions").mkdir()
    config = {
        "version": 2,
        "name": "toy",
        "checkpoints": {
            f"checkpoint_{i}": {"version": 1, "order": i, "state": "Core Tests"}
            for i in range(1, n + 1)
        },
    }
    (prob / "config.yaml").write_text("# CANARY\n" + yaml.safe_dump(config, sort_keys=False))
    for i in range(1, n + 1):
        (prob / f"checkpoint_{i}.md").write_text(f"spec {i}")
        (prob / "tests" / f"test_checkpoint_{i}.py").write_text("")
        (prob / "tests" / "data" / f"checkpoint_{i}").mkdir()
        (prob / "solutions" / f"checkpoint_{i}").mkdir()
    (prob / "tests" / "conftest.py").write_text("")
    return prob


def test_smoke_problem_keeps_first_n_checkpoints(tmp_path: Path):
    src = _make_problem(tmp_path / "src", 4)
    dest = make_smoke_problem.truncate_problem(src, tmp_path / "out", keep=2)

    config = yaml.safe_load((dest / "config.yaml").read_text())
    assert list(config["checkpoints"]) == ["checkpoint_1", "checkpoint_2"]
    assert (dest / "config.yaml").read_text().startswith("# CANARY\n")
    remaining = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*checkpoint_*"))
    assert remaining == [
        "checkpoint_1.md",
        "checkpoint_2.md",
        "solutions/checkpoint_1",
        "solutions/checkpoint_2",
        "tests/data/checkpoint_1",
        "tests/data/checkpoint_2",
        "tests/test_checkpoint_1.py",
        "tests/test_checkpoint_2.py",
    ]
    assert (dest / "tests" / "conftest.py").exists()
    # Source untouched.
    assert (src / "checkpoint_4.md").exists()


def test_manifest_reads_pidt_template_pins(tmp_path: Path):
    agent = ROOT / "agents" / "pidt.yaml"
    if not agent.exists():
        pytest.skip("agents/pidt.yaml not present")
    info = manifest.agent_info(agent)
    assert info["pi_version"] == "1.3.0"
    assert str(info["pi_dev_team_sha"]).startswith("3c34bf5")


def test_manifest_write_and_finish(tmp_path: Path):
    run = tmp_path / "run"
    rc = manifest.main(
        [
            "write", "--run-dir", str(run), "--arm", "cc-clean", "--subset", "smoke",
            "--repeat", "1", "--agent", str(ROOT / "agents" / "cc-clean.yaml"),
            "--model", "claude_code_oauth/opus-5.5", "--prompt", "just-solve",
            "--environment", "docker-python3.12-uv", "--command", "slop-code run ...",
        ]
    )
    assert rc == 0
    data = json.loads((run / "manifest.json").read_text())
    assert data["arm"] == "cc-clean" and data["repeat"] == 1
    assert data["clean"] == {"status": "PENDING"}
    assert data["scb"]["sha"]
    assert manifest.main(["finish", "--run-dir", str(run), "--exit-code", "0"]) == 0
    assert json.loads((run / "manifest.json").read_text())["status"] == "finished"


@pytest.mark.parametrize(
    "args",
    [["bogus", "smoke"], ["pidt", "bogus"], ["pidt"], ["pidt", "smoke", "0"]],
)
def test_run_sh_rejects_bad_args(args):
    proc = subprocess.run(
        [str(ROOT / "run.sh"), "--dry-run", *args], capture_output=True, text=True
    )
    assert proc.returncode == 2


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_run_sh_dry_run_resolves_pidt_smoke():
    proc = subprocess.run(
        [str(ROOT / "run.sh"), "--dry-run", "pidt", "smoke", "7"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert "-pidt-smoke-r7" in proc.stdout
    assert "--problem file_backup" in proc.stdout
    assert "SCBENCH_PROBLEMS_PATH=" in proc.stdout
    assert '"image": "pi-1.3.0-pdt3c34bf5-python3.12"' in proc.stdout
    # Dry run creates nothing.
    assert not list((ROOT / "results").glob("*-pidt-smoke-r7"))
