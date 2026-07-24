import asyncio

import pytest

from tests.conftest import register_tools
from tests.test_audio_file import write_aiff, write_wav


@pytest.fixture
def project_tools(mock_client):
    return register_tools("project_tools", mock_client)


@pytest.fixture
def export_dir(tmp_path):
    """A subfolder: exporting to the home folder root is rejected on purpose."""
    d = tmp_path / "Music"
    d.mkdir()
    return d


def exporting(mock_client, writer):
    """Make Export2 actually produce a file, the way Audacity would."""
    async def _export(command, extra_params=None, **params):
        writer(params["Filename"])
        return {"success": True, "raw": "", "message": "", "data": {}}

    mock_client.execute_long.side_effect = _export


def run_export(project_tools, path, **kwargs):
    return asyncio.run(project_tools["project_export_audio"].fn(path=str(path), **kwargs))


class TestWholeProjectSelection:
    def test_selects_everything_before_exporting(self, project_tools, mock_client, export_dir):
        """Export2 exports the SELECTION — a 78s project exported as 3.8s
        because nothing established one first."""
        exporting(mock_client, lambda p: write_wav(__import__("pathlib").Path(p)))
        run_export(project_tools, export_dir / "out.wav")
        commands = [c[1][0] for c in mock_client.mock_calls if c[0] in ("execute", "execute_long")]
        assert commands == ["SelAllTracks", "SelectAll", "Export2"]

    def test_selection_only_when_asked(self, project_tools, mock_client, export_dir):
        exporting(mock_client, lambda p: write_wav(__import__("pathlib").Path(p)))
        run_export(project_tools, export_dir / "out.wav", whole_project=False)
        commands = [c[1][0] for c in mock_client.mock_calls if c[0] in ("execute", "execute_long")]
        assert commands == ["Export2"]


class TestExportVerification:
    def test_a_real_wav_reports_no_warning(self, project_tools, mock_client, export_dir):
        import pathlib

        exporting(mock_client, lambda p: write_wav(pathlib.Path(p), rate=44100))
        result = run_export(project_tools, export_dir / "out.wav", num_channels=1)
        assert result["verified"]["container"] == "wav"
        assert result["verified"]["sample_rate"] == 44100
        assert "warnings" not in result

    def test_aiff_written_into_a_wav_path_is_called_out(self, project_tools, mock_client, export_dir):
        import pathlib

        exporting(mock_client, lambda p: write_aiff(pathlib.Path(p), rate=44100))
        result = run_export(project_tools, export_dir / "out.wav", num_channels=1)
        assert result["verified"]["container"] == "aiff"
        assert any("AIFF data despite the .wav extension" in w for w in result["warnings"])

    def test_unexpected_sample_rate_is_called_out(self, project_tools, mock_client, export_dir):
        import pathlib

        exporting(mock_client, lambda p: write_wav(pathlib.Path(p), rate=384000))
        result = run_export(project_tools, export_dir / "out.wav", num_channels=1)
        assert any("384000 Hz" in w for w in result["warnings"])

    def test_channel_count_mismatch_is_called_out(self, project_tools, mock_client, export_dir):
        import pathlib

        exporting(mock_client, lambda p: write_wav(pathlib.Path(p), channels=1))
        result = run_export(project_tools, export_dir / "out.wav", num_channels=2)
        assert any("not the 2 requested" in w for w in result["warnings"])

    def test_success_with_no_file_at_all_is_called_out(self, project_tools, mock_client, export_dir):
        exporting(mock_client, lambda p: None)
        result = run_export(project_tools, export_dir / "out.wav")
        assert any("no file was written" in w for w in result["warnings"])
