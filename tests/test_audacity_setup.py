"""Setup's config write, and the guard on it.

Audacity rewrites audacity.cfg when it quits. An edit made while it is open is
reverted the next time the user closes the app, and the edit reports success on
the way - which is the failure install.sh has today.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import audacity_setup  # noqa: E402


def write_cfg(tmp_path, body):
    cfg = tmp_path / "audacity.cfg"
    cfg.write_text(body)
    return str(cfg)


class TestEnableModule:
    def test_flips_an_existing_disabled_setting(self, tmp_path):
        cfg = write_cfg(tmp_path, "[Module]\nmod-script-pipe=0\nmod-nyq-workbench=0\n")
        result = audacity_setup.enable_module(cfg, running=False)
        assert result["changed"] is True
        assert "mod-script-pipe=1" in pathlib.Path(cfg).read_text()
        assert "mod-nyq-workbench=0" in pathlib.Path(cfg).read_text()

    def test_flips_ask_as_well_as_disabled(self, tmp_path):
        # "Ask" prompts on every launch and creates no pipes until someone
        # clicks through, so leaving it alone would look like setup did nothing.
        cfg = write_cfg(tmp_path, "[Module]\nmod-script-pipe=2\n")
        audacity_setup.enable_module(cfg, running=False)
        assert "mod-script-pipe=1" in pathlib.Path(cfg).read_text()

    def test_adds_the_setting_when_it_is_absent(self, tmp_path):
        cfg = write_cfg(tmp_path, "[Module]\nmod-nyq-workbench=0\n")
        audacity_setup.enable_module(cfg, running=False)
        assert "mod-script-pipe=1" in pathlib.Path(cfg).read_text()

    def test_is_a_no_op_when_already_enabled(self, tmp_path):
        cfg = write_cfg(tmp_path, "[Module]\nmod-script-pipe=1\n")
        result = audacity_setup.enable_module(cfg, running=False)
        assert result["changed"] is False

    def test_backs_the_file_up_before_writing(self, tmp_path):
        cfg = write_cfg(tmp_path, "[Module]\nmod-script-pipe=0\n")
        audacity_setup.enable_module(cfg, running=False)
        assert (tmp_path / "audacity.cfg.bak").read_text() == "[Module]\nmod-script-pipe=0\n"

    def test_refuses_while_audacity_is_running(self, tmp_path):
        original = "[Module]\nmod-script-pipe=0\n"
        cfg = write_cfg(tmp_path, original)

        result = audacity_setup.enable_module(cfg, running=True)

        assert result["changed"] is False
        assert result["refused"] == "audacity-running"
        assert pathlib.Path(cfg).read_text() == original, "the file must be untouched"
        assert "quit" in result["message"].lower()

    def test_refuses_when_it_cannot_tell_whether_audacity_is_running(self, tmp_path):
        # None means the probe failed. Writing anyway risks a silent revert, and
        # the cost of being wrong is asymmetric: refusing wastes a minute,
        # writing wastes the user's trust in the setting.
        original = "[Module]\nmod-script-pipe=0\n"
        cfg = write_cfg(tmp_path, original)

        result = audacity_setup.enable_module(cfg, running=None)

        assert result["changed"] is False
        assert result["refused"] == "unknown-state"
        assert pathlib.Path(cfg).read_text() == original
