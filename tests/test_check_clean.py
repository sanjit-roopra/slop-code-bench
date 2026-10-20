"""Tests for scripts/check_clean.py using trimmed real traces and synthetic dirty ones."""

from __future__ import annotations

import copy
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_clean as cc  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _lines(name: str) -> list[str]:
    return (FIX / name).read_text().splitlines()


def _events(name: str) -> list[dict]:
    return list(cc.iter_events(_lines(name)))


def _write_trace(run: Path, problem: str, ckpt: str, lines: list[str]) -> Path:
    agent = run / problem / ckpt / "agent"
    agent.mkdir(parents=True)
    path = agent / "stdout.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


# --- Claude Code ------------------------------------------------------------


def test_stock_cc_init_is_clean():
    r = cc.check_cc_events(_events("cc_init_clean.jsonl"), expect_version="2.1.304")
    assert r.status == "CLEAN", r.reasons
    assert r.info["claude_code_versions"] == ["2.1.304"]


def test_allowlist_matches_stock_fixture_exactly():
    init = _events("cc_init_clean.jsonl")[0]
    assert set(init["skills"]) == cc.CC_STOCK_SKILLS
    assert set(init["agents"]) == cc.CC_STOCK_AGENTS
    assert all(p["path"] == "builtin" for p in init["plugins"])


def test_dirty_cc_init_reports_each_leak():
    r = cc.check_cc_events(_events("cc_init_dirty.jsonl"))
    assert r.status == "DIRTY"
    joined = "\n".join(r.reasons)
    assert "non-builtin plugin 'dev-team'" in joined
    assert "MCP servers present" in joined
    assert "namespaced (plugin) skill 'dev-team:plan'" in joined
    assert "namespaced (plugin) agent 'dev-team:software-engineer'" in joined
    assert "namespaced (plugin) slash command 'dev-team:build'" in joined


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda e: e["plugins"].append({"name": "x", "path": "/p"}), "non-builtin plugin"),
        (lambda e: e.__setitem__("mcp_servers", [{"name": "m"}]), "MCP servers"),
        (lambda e: e["skills"].append("dev-team:x"), "namespaced (plugin) skill"),
        (lambda e: e["skills"].append("my-user-skill"), "not in stock allowlist"),
        (lambda e: e["agents"].append("my-agent"), "not in stock allowlist"),
    ],
)
def test_single_cc_leak_is_dirty(mutate, needle):
    init = copy.deepcopy(_events("cc_init_clean.jsonl")[0])
    mutate(init)
    r = cc.check_cc_events([init])
    assert r.status == "DIRTY"
    assert any(needle in reason for reason in r.reasons)


def test_cc_version_mismatch_is_dirty():
    r = cc.check_cc_events(_events("cc_init_clean.jsonl"), expect_version="2.0.51")
    assert r.status == "DIRTY"


def test_cc_trace_without_init_is_unknown():
    r = cc.check_cc_events([{"type": "result", "subtype": "success"}])
    assert r.status == "UNKNOWN"


# --- pi ---------------------------------------------------------------------


def test_pidt_trace_is_clean_for_pidt_arm():
    r = cc.check_pi_events(_events("pi_pidt_clean.jsonl"), dev_team=True)
    assert r.status == "CLEAN", r.reasons
    assert r.info["dev_team_loaded"] is True
    assert "github-copilot/gpt-6.1-sol" in r.info["models"]
    assert "github-copilot/gpt-6.1-sol" in r.info["subagent_models"]


def test_pidt_trace_is_dirty_for_pi_stock_arm():
    r = cc.check_pi_events(_events("pi_pidt_clean.jsonl"), dev_team=False)
    assert r.status == "DIRTY"
    joined = "\n".join(r.reasons)
    assert "tool 'dev_team_subagent' from an unexpected extension" in joined
    assert "unexpected system-prompt section 'dev_team'" in joined
    assert "skill loaded from '/opt/pi-dev-team/skills/plan/SKILL.md'" in joined


def test_pi_extra_extension_is_dirty():
    events = copy.deepcopy(_events("pi_pidt_clean.jsonl"))
    for e in events:
        msg = e.get("message") or {}
        if msg.get("role") == "system":
            msg["toolsAdded"].append({"name": "subagent"})
            msg["sections"]["other_ext"] = "..."
    events.append(
        {
            "type": "message_end",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": '<skill name="x" location="/root/.pi/agent/skills/x/SKILL.md">'}],
            },
        }
    )
    events.append({"type": "message_end", "message": {"role": "custom", "customType": "other-ext-start"}})
    r = cc.check_pi_events(events, dev_team=True)
    assert r.status == "DIRTY"
    joined = "\n".join(r.reasons)
    assert "tool 'subagent'" in joined
    assert "section 'other_ext'" in joined
    assert "/root/.pi/agent/skills/x/SKILL.md" in joined
    assert "'other-ext-start'" in joined


def test_pidt_arm_without_dev_team_loaded_is_dirty():
    events = [
        {
            "type": "message_end",
            "message": {
                "role": "system",
                "sections": {"preamble": "", "tools": "", "cwd": ""},
                "toolsAdded": [{"name": "read"}, {"name": "bash"}],
            },
        }
    ]
    assert cc.check_pi_events(events, dev_team=True).status == "DIRTY"
    assert cc.check_pi_events(events, dev_team=False).status == "CLEAN"


# --- run level / CLI --------------------------------------------------------


def test_check_run_and_manifest_update(tmp_path: Path):
    run = tmp_path / "run"
    _write_trace(run, "xjq", "checkpoint_1", _lines("cc_init_clean.jsonl"))
    _write_trace(run, "xjq", "checkpoint_2", _lines("cc_init_clean.jsonl"))
    (run / "manifest.json").write_text(json.dumps({"arm": "cc-clean"}))

    rc = cc.main([str(run), "--arm", "cc-clean", "--expect-cc-version", "2.1.304"])

    assert rc == 0
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["clean"]["status"] == "CLEAN"
    assert manifest["clean"]["traces_checked"] == 2
    assert (run / "clean_check.json").exists()


def test_one_dirty_checkpoint_makes_run_dirty(tmp_path: Path):
    run = tmp_path / "run"
    _write_trace(run, "xjq", "checkpoint_1", _lines("cc_init_clean.jsonl"))
    _write_trace(run, "xjq", "checkpoint_2", _lines("cc_init_dirty.jsonl"))
    assert cc.main([str(run), "--arm", "cc-clean"]) == 1


def test_run_without_traces_is_unknown(tmp_path: Path):
    (tmp_path / "run").mkdir()
    assert cc.main([str(tmp_path / "run"), "--arm", "pidt"]) == 2


def test_compressed_artifacts_are_read(tmp_path: Path):
    run = tmp_path / "run"
    ckpt = run / "xjq" / "checkpoint_1"
    ckpt.mkdir(parents=True)
    data = (FIX / "pi_pidt_clean.jsonl").read_bytes()
    with tarfile.open(ckpt / "agent.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo("agent/stdout.jsonl")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    report = cc.check_run(run, "pidt")
    assert report["status"] == "CLEAN"
    assert report["traces_checked"] == 1


def test_fixtures_contain_no_secrets():
    import re

    pattern = re.compile(r"gh[opusr]_[A-Za-z0-9]{20,}|github_pat_|sk-ant-|eyJ[A-Za-z0-9_-]{10,}|Bearer ")
    for path in FIX.glob("*"):
        assert not pattern.search(path.read_text()), path
