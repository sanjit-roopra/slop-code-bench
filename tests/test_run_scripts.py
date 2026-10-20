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


_SCB_PY = ["uv", "run", "--quiet", "--project", str(ROOT / "vendor" / "slop-code-bench"), "python"]
_needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")

# Model id each arm's run config must resolve to on its own (no --model).
_RUN_CONFIG_INTERNAL_NAMES = {
    "cc-clean": "claude-opus-5-5",
    "pidt": "gpt-6.1-sol",
    "pi-stock": "gpt-6.1-sol",
    "pidt-opus": "claude-opus-5.5",  # Copilot id, not the Anthropic claude-opus-5-5
}


@_needs_uv
@pytest.mark.parametrize("run_config", sorted((ROOT / "runs").glob("*.yaml")), ids=lambda p: p.stem)
def test_run_config_resolves_to_arm_model_without_override(run_config: Path):
    arm = run_config.stem.rsplit("_lite", 1)[0]
    proc = subprocess.run(
        [*_SCB_PY, "scripts/resolve_config.py", "--config", str(run_config.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env={**__import__("os").environ, "TMPDIR": "/tmp"},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["errors"] == []
    assert out["model_internal_name"] == _RUN_CONFIG_INTERNAL_NAMES[arm]


@_needs_uv
@pytest.mark.parametrize(
    ("agent", "model", "expected"),
    [
        ("cc-clean", "claude_code_oauth/opus-5.5", "claude-opus-5-5"),
        ("pidt", "github-copilot/gpt-6.1-sol", "github-copilot/gpt-6.1-sol"),
        ("pidt", "github-copilot/copilot-claude-opus-5.5", "github-copilot/claude-opus-5.5"),
        ("pi-stock", "github-copilot/gpt-6.1-sol", "github-copilot/gpt-6.1-sol"),
    ],
)
def test_expected_runtime_model(agent: str, model: str, expected: str):
    info = manifest.agent_info(ROOT / "agents" / f"{agent}.yaml")
    assert manifest.expected_runtime_model(model, info.get("type")) == expected


_SNAPSHOT_PROBE = r"""
import json, sys
from pathlib import Path
import yaml
from slop_code.execution.snapshot import _walk_candidates
env = yaml.safe_load(Path(sys.argv[1]).read_text())
kept, ignored = _walk_candidates(Path(sys.argv[2]), set(env["snapshot"]["ignore_globs"]), set())
print(json.dumps(sorted(p.as_posix() for p in kept)))
"""


@_needs_uv
def test_pi_env_snapshot_keeps_only_claude_memory_and_drops_git(tmp_path: Path):
    ws = tmp_path / "ws"
    files = [
        "main.py",
        ".gitignore",
        "plans/plan.md",
        ".claude/memory/progress.md",
        ".claude/memory/sub/notes.md",
        ".claude/mcp.json",
        ".claude/metrics/x.jsonl",
        ".claude/settings.json",
        ".claude/commands/foo.md",
        ".claude/m",
        ".claude/memo",
        ".claude/memory",  # a file, not the memory/ dir (in another workspace)
        ".claude/memoryx/y",
        ".git/HEAD",
        ".git/objects/ab/cd",
    ]
    for rel in files:
        if rel == ".claude/memory":
            continue
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    proc = subprocess.run(
        [*_SCB_PY, "-c", _SNAPSHOT_PROBE, str(ROOT / "environments" / "docker-python3.12-uv-pidt.yaml"), str(ws)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [
        ".claude/memory/progress.md",
        ".claude/memory/sub/notes.md",
        ".gitignore",
        "main.py",
        "plans/plan.md",
    ]

    # A plain file named .claude/memory is dropped too.
    ws2 = tmp_path / "ws2"
    (ws2 / ".claude").mkdir(parents=True)
    (ws2 / ".claude" / "memory").write_text("x")
    proc = subprocess.run(
        [*_SCB_PY, "-c", _SNAPSHOT_PROBE, str(ROOT / "environments" / "docker-python3.12-uv-pidt.yaml"), str(ws2)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []


def test_manifest_hashes_configs_and_records_images(tmp_path: Path):
    run = tmp_path / "run"
    rc = manifest.main(
        [
            "write", "--run-dir", str(run), "--arm", "pidt", "--subset", "lite",
            "--repeat", "1", "--agent", str(ROOT / "agents" / "pidt.yaml"),
            "--model", "github-copilot/gpt-6.1-sol", "--prompt", str(ROOT / "prompts" / "pidt.jinja"),
            "--environment", str(ROOT / "environments" / "docker-python3.12-uv-pidt.yaml"),
            "--run-config", str(ROOT / "runs" / "pidt_lite.yaml"),
        ]
    )
    assert rc == 0
    data = json.loads((run / "manifest.json").read_text())
    assert len(data["run_config"]["sha256"]) == 64
    assert len(data["environment"]["sha256"]) == 64
    assert data["model"]["expected_runtime"] == "github-copilot/gpt-6.1-sol"
    assert data["timeout_s"] == 10800
    assert data["images"]["agent"]["ref"] == "slop-code:pi-1.3.0-pdt3c34bf5-python3.12"
    assert data["images"]["base"]["ref"] == "slop-code:python3.12"
    assert data["images"]["upstream"]["ref"] == "ghcr.io/astral-sh/uv:python3.12-trixie-slim"
    assert "changes" in data["eval_coding"]

    # A bare SCB environment name resolves into SCB's configs/environments.
    env = manifest.file_info("docker-python3.12-uv", manifest.SCB / "configs" / "environments")
    assert env is not None and env.get("sha256")


def test_git_info_counts_untracked_files(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert manifest.git_info(repo)["dirty"] is False
    (repo / "agents").mkdir()
    (repo / "agents" / "new_agent.yaml").write_text("x")
    info = manifest.git_info(repo)
    assert info["dirty"] is True
    assert info["changes"] == ["?? agents/new_agent.yaml"]


def test_run_sh_resume_rejects_other_arm(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"arm": "pidt", "subset": "lite"}))
    proc = subprocess.run(
        [str(ROOT / "run.sh"), "--dry-run", "--resume", str(run), "pi-stock", "lite"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "was run as arm 'pidt', not 'pi-stock'" in proc.stderr


def test_run_sh_resume_requires_manifest(tmp_path: Path):
    proc = subprocess.run(
        [str(ROOT / "run.sh"), "--dry-run", "--resume", str(tmp_path), "pidt", "lite"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "no manifest.json" in proc.stderr
