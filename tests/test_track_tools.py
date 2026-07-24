import asyncio
import json

import pytest

from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode
from tests.conftest import register_tools


@pytest.fixture
def track_tools(mock_client):
    return register_tools("track_tools", mock_client)


def tracks_reply(tracks):
    return {"success": True, "raw": "", "message": json.dumps(tracks), "data": {}}


def replies(mock_client, tracks):
    """SetTrackStatus answers OK; the follow-up GetInfo answers with `tracks`."""
    ok = {"success": True, "raw": "", "message": "", "data": {}}

    async def _execute(command, extra_params=None, **params):
        if command == "GetInfo":
            return tracks_reply(tracks)
        return dict(ok)

    mock_client.execute.side_effect = _execute


class TestMuteAndSoloReadBack:
    """SetTrackStatus reports success for Mute and Solo and leaves the flag
    alone (Audacity 3.7.8). Reading back is the only way a caller can tell
    (issue #8)."""

    def test_mute_that_did_not_take_is_warned_about(self, track_tools, mock_client):
        replies(mock_client, [{"name": "Audio", "mute": 0, "solo": 0}])
        result = asyncio.run(track_tools["track_mute"].fn(track=0, mute=True))
        assert "warnings" in result
        assert "Mute did not change" in result["warnings"][0]

    def test_mute_that_took_is_not_warned_about(self, track_tools, mock_client):
        replies(mock_client, [{"name": "Audio", "mute": 1, "solo": 0}])
        result = asyncio.run(track_tools["track_mute"].fn(track=0, mute=True))
        assert "warnings" not in result

    def test_unmute_is_verified_the_same_way(self, track_tools, mock_client):
        replies(mock_client, [{"name": "Audio", "mute": 1}])
        result = asyncio.run(track_tools["track_mute"].fn(track=0, mute=False))
        assert "Mute did not change" in result["warnings"][0]

    def test_flag_names_are_matched_case_insensitively(self, track_tools, mock_client):
        """GetInfo answers with lowercase keys and SetTrackStatus takes
        capitalised ones; neither casing may slip past the comparison."""
        replies(mock_client, [{"name": "Audio", "Mute": 0, "Solo": 0}])
        result = asyncio.run(track_tools["track_mute"].fn(track=0, mute=True))
        assert "Mute did not change" in result["warnings"][0]

    def test_set_properties_names_both_flags(self, track_tools, mock_client):
        replies(mock_client, [{"name": "Audio", "mute": 0, "solo": 0}])
        result = asyncio.run(
            track_tools["track_set_properties"].fn(track=0, mute=True, solo=True)
        )
        assert "Mute and Solo did not change" in result["warnings"][0]

    def test_no_read_back_when_no_flag_was_set(self, track_tools, mock_client):
        replies(mock_client, [{"name": "Audio", "mute": 0}])
        asyncio.run(track_tools["track_set_properties"].fn(track=0, name="Voice"))
        commands = [c.args[0] for c in mock_client.execute.await_args_list]
        assert commands == ["SetTrackStatus"]

    def test_a_gain_only_change_is_not_verified(self, track_tools, mock_client):
        replies(mock_client, [{"name": "Audio", "mute": 0}])
        result = asyncio.run(track_tools["track_set_properties"].fn(track=0, gain=-6.0))
        assert "warnings" not in result


class TestReadBackFailureIsSilent:
    """The read-back is a diagnostic. It must never turn a working call into
    an error just because GetInfo answered oddly."""

    def test_unparseable_get_info(self, track_tools, mock_client):
        async def _execute(command, extra_params=None, **params):
            if command == "GetInfo":
                return {"success": True, "raw": "", "message": "not json", "data": {}}
            return {"success": True, "raw": "", "message": "", "data": {}}

        mock_client.execute.side_effect = _execute
        result = asyncio.run(track_tools["track_mute"].fn(track=0))
        assert "warnings" not in result

    def test_get_info_that_raises(self, track_tools, mock_client):
        async def _execute(command, extra_params=None, **params):
            if command == "GetInfo":
                raise AudacityMCPError(ErrorCode.PIPE_TIMEOUT, "no answer")
            return {"success": True, "raw": "", "message": "", "data": {}}

        mock_client.execute.side_effect = _execute
        result = asyncio.run(track_tools["track_mute"].fn(track=0))
        assert "warnings" not in result

    def test_track_index_beyond_what_get_info_returned(self, track_tools, mock_client):
        replies(mock_client, [{"name": "Audio", "mute": 0}])
        result = asyncio.run(track_tools["track_mute"].fn(track=3))
        assert "warnings" not in result

    def test_a_track_entry_without_the_flag(self, track_tools, mock_client):
        replies(mock_client, [{"name": "Audio"}])
        result = asyncio.run(track_tools["track_mute"].fn(track=0))
        assert "warnings" not in result
