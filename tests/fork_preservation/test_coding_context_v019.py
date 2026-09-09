# Fork-preservation residue extracted during the v2026.8.16 integration.
# These tests were present at fork head 896a5ea3b2 but were removed or
# reorganized upstream; keep them as behavior gates for retained features.
"""Tests for agent.coding_context — RuntimeMode seam, resolver, toolset, git probe."""

import json
import os
import subprocess
import shutil
from pathlib import Path

import pytest

from agent import coding_context as cc




def _git_init(path):
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(path),
    }
    # Commit a source file so the fixture is a real *code* workspace: a bare git
    # repo with no code no longer flips into the coding posture (see
    # _detect_profile_name / _has_code_files), so "a code repo" needs code.
    (Path(path) / "main.py").write_text("print('hi')\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "init commit"],
    ):
        subprocess.run([shutil.which("git"), "-C", str(path), *args], check=True, env=env)


# ── resolver ──────────────────────────────────────────────────────────────

class TestIsCodingContext:
    def test_off_never_activates(self, tmp_path):
        _git_init(tmp_path)
        cfg = {"agent": {"coding_context": "off"}}
        assert cc.is_coding_context(platform="cli", cwd=tmp_path, config=cfg) is False

    def test_on_forces_even_without_git(self, tmp_path):
        cfg = {"agent": {"coding_context": "on"}}
        assert cc.is_coding_context(platform="telegram", cwd=tmp_path, config=cfg) is True

    def test_auto_requires_git_repo(self, tmp_path):
        cfg = {"agent": {"coding_context": "auto"}}
        assert cc.is_coding_context(platform="cli", cwd=tmp_path, config=cfg) is False
        _git_init(tmp_path)
        assert cc.is_coding_context(platform="cli", cwd=tmp_path, config=cfg) is True


    def test_auto_skips_messaging_surfaces(self, tmp_path):
        _git_init(tmp_path)
        cfg = {"agent": {"coding_context": "auto"}}
        assert cc.is_coding_context(platform="discord", cwd=tmp_path, config=cfg) is False
        assert cc.is_coding_context(platform="tui", cwd=tmp_path, config=cfg) is True



# ── toolset substitution ────────────────────────────────────────────────────

class TestCodingSelection:


    def test_on_is_prompt_only(self, tmp_path):
        cfg = {"agent": {"coding_context": "on"}}
        assert cc.coding_selection(platform="cli", cwd=tmp_path, config=cfg) is None
        assert cc.is_coding_context(platform="cli", cwd=tmp_path, config=cfg) is True

    def test_focus_requires_workspace(self, tmp_path):
        # focus inherits auto's detection gate — bare dir stays general.
        cfg = {"agent": {"coding_context": "focus"}}
        assert cc.coding_selection(platform="cli", cwd=tmp_path, config=cfg) is None

    def test_none_when_inactive(self, tmp_path):
        cfg = {"agent": {"coding_context": "off"}}
        assert cc.coding_selection(platform="cli", cwd=tmp_path, config=cfg) is None



# ── git/workspace probe ─────────────────────────────────────────────────────



# ── project facts (verify-loop detection) ───────────────────────────────────

class TestProjectFacts:
    def test_package_json_scripts_surface_verify_commands(self, tmp_path):
        _git_init(tmp_path)
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest", "lint": "eslint .", "dev": "vite"}})
        )
        (tmp_path / "pnpm-lock.yaml").write_text("")
        block = cc.build_coding_workspace_block(tmp_path)
        assert "Project: package.json (pnpm)" in block
        assert "pnpm run test" in block and "pnpm run lint" in block
        # Non-verify scripts (dev servers, …) stay out of the snapshot.
        assert "run dev" not in block

    def test_pytest_config_and_run_tests_script(self, tmp_path):
        _git_init(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "run_tests.sh").write_text("#!/bin/sh\n")
        block = cc.build_coding_workspace_block(tmp_path)
        assert "scripts/run_tests.sh" in block
        assert "pytest" in block.split("Verify:")[1]

    def test_makefile_verify_targets_only(self, tmp_path):
        _git_init(tmp_path)
        (tmp_path / "Makefile").write_text("test:\n\tgo test ./...\n\ndeploy:\n\t./deploy.sh\n")
        block = cc.build_coding_workspace_block(tmp_path)
        assert "make test" in block
        assert "make deploy" not in block

    def test_context_files_listed(self, tmp_path):
        _git_init(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# rules")
        block = cc.build_coding_workspace_block(tmp_path)
        assert "Context files: AGENTS.md" in block


    def test_marker_only_project_gets_snapshot_without_git(self, tmp_path):
        # A non-git project (manifest only) still gets a workspace snapshot —
        # just without the git lines.
        (tmp_path / "package.json").write_text("{}")
        block = cc.build_coding_workspace_block(tmp_path)
        assert f"Root: {tmp_path.resolve()}" in block
        assert "package.json" in block
        assert "Branch:" not in block and "Status:" not in block

    def test_malformed_package_json_is_ignored(self, tmp_path):
        _git_init(tmp_path)
        (tmp_path / "package.json").write_text("{not json")
        block = cc.build_coding_workspace_block(tmp_path)
        assert "Project: package.json" in block
        assert "Verify:" not in block



    def test_project_facts_for_none_outside_workspace(self, tmp_path):
        assert cc.project_facts_for(tmp_path) is None


# ── $HOME dotfiles guard ────────────────────────────────────────────────────

class TestHomeDotfilesGuard:

    def test_marker_at_home_is_not_a_project_signal(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "Makefile").write_text("all:\n")
        monkeypatch.setattr(Path, "home", lambda: home)
        cfg = {"agent": {"coding_context": "auto"}}
        assert cc.is_coding_context(platform="cli", cwd=home, config=cfg) is False


    def test_on_mode_bypasses_the_guard(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        cfg = {"agent": {"coding_context": "on"}}
        assert cc.is_coding_context(platform="cli", cwd=home, config=cfg) is True


# ── prompt assembly integration ─────────────────────────────────────────────



# ── RuntimeMode seam ────────────────────────────────────────────────────────

class TestRuntimeMode:


    def test_is_frozen(self, tmp_path):
        mode = cc.resolve_runtime_mode(platform="cli", cwd=tmp_path, config={})
        with pytest.raises(Exception):
            mode.profile = cc.CODING_PROFILE  # type: ignore[misc]

    def test_system_blocks_include_brief_and_workspace(self, tmp_path):
        _git_init(tmp_path)
        mode = cc.resolve_runtime_mode(platform="cli", cwd=tmp_path, config={"agent": {"coding_context": "on"}})
        blocks = mode.system_blocks()
        assert any("coding agent" in b for b in blocks)
        assert any("Workspace" in b for b in blocks)

    def test_coding_instructions_append_their_own_block(self, tmp_path):
        _git_init(tmp_path)
        cfg = {
            "agent": {
                "coding_context": "on",
                "coding_instructions": "Clean the diff before commit.",
            }
        }
        mode = cc.resolve_runtime_mode(platform="cli", cwd=tmp_path, config=cfg)
        blocks = mode.system_blocks()
        # The brief stays block 0 (byte-stable, cache-keyed independently); the
        # operator instructions ride a separate trailing block.
        assert blocks[0] == cc.CODING_AGENT_GUIDANCE
        assert any("Clean the diff before commit." in b for b in blocks[1:])

    def test_coding_instructions_accept_a_list(self, tmp_path):
        _git_init(tmp_path)
        cfg = {
            "agent": {
                "coding_context": "on",
                "coding_instructions": ["No tsc/lint on UI.", "Clean the diff."],
            }
        }
        mode = cc.resolve_runtime_mode(platform="cli", cwd=tmp_path, config=cfg)
        instr_block = mode.system_blocks()[-1]
        assert "No tsc/lint on UI." in instr_block
        assert "Clean the diff." in instr_block


    def test_toolset_selection_gated_on_focus(self, tmp_path):
        _git_init(tmp_path)
        focus = cc.resolve_runtime_mode(platform="cli", cwd=tmp_path, config={"agent": {"coding_context": "focus"}})
        sel = focus.toolset_selection()
        assert sel and sel[0] == cc.CODING_TOOLSET
        # auto/on resolve the coding profile but stay prompt-only.
        for raw in ("auto", "on"):
            mode = cc.resolve_runtime_mode(platform="cli", cwd=tmp_path, config={"agent": {"coding_context": raw}})
            assert mode.is_coding is True
            assert mode.toolset_selection() is None


# ── edit-format steering (per-model harness tuning) ──────────────────────────

class TestEditFormatSteering:


    def test_anthropic_family_gets_replace_nudge(self, tmp_path):
        _git_init(tmp_path)
        mode = cc.resolve_runtime_mode(
            platform="cli", cwd=tmp_path,
            config={"agent": {"coding_context": "on"}},
            model="anthropic/claude-opus-4.8",
        )
        brief = mode.system_blocks()[0]
        assert "mode='replace'" in brief
        assert "write_file" in brief  # new files authored, not patched

    def test_unknown_model_keeps_neutral_brief(self, tmp_path):
        # No edit-format line appended — brief equals the bare profile guidance.
        _git_init(tmp_path)
        mode = cc.resolve_runtime_mode(
            platform="cli", cwd=tmp_path,
            config={"agent": {"coding_context": "on"}}, model="acme/foo-1",
        )
        assert mode.system_blocks()[0] == cc.CODING_AGENT_GUIDANCE

    def test_no_model_keeps_neutral_brief(self, tmp_path):
        _git_init(tmp_path)
        mode = cc.resolve_runtime_mode(
            platform="cli", cwd=tmp_path,
            config={"agent": {"coding_context": "on"}},
        )
        assert mode.system_blocks()[0] == cc.CODING_AGENT_GUIDANCE

    def test_general_posture_emits_nothing_regardless_of_model(self, tmp_path):
        # Edit steering only fires inside the coding posture.
        mode = cc.resolve_runtime_mode(
            platform="telegram", cwd=tmp_path, config={}, model="openai/gpt-5.4",
        )
        assert mode.system_blocks() == []


# ── profile registry ────────────────────────────────────────────────────────

class TestProfiles:
    def test_registered_profiles(self):
        assert cc.get_profile("coding") is cc.CODING_PROFILE
        assert cc.get_profile("general") is cc.GENERAL_PROFILE

    def test_unknown_profile_falls_back_to_general(self):
        assert cc.get_profile("nonsense") is cc.GENERAL_PROFILE




# ── detection signals ───────────────────────────────────────────────────────
