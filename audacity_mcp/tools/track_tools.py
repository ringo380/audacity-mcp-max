from mcp.server.fastmcp import FastMCP
from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode
from audacity_mcp_shared.constants import COMMON_SAMPLE_RATES, MAX_TRACKS


def register(mcp: FastMCP):
    from audacity_mcp.main import client

    async def _verify_track_flags(track: int, expected: dict) -> list[str]:
        """Read the track back and report flags that did not take.

        SetTrackStatus answers "BatchCommand finished: OK" for Mute and Solo and
        then leaves the flag alone (Audacity 3.7.8). Name on the same command
        does apply, so the targeting is right and the write is the part that is
        silently dropped. A caller cannot see that without reading back.
        """
        import json

        if not expected:
            return []
        try:
            info = await client.execute("GetInfo", Type="Tracks")
            tracks = json.loads(info.get("message", "") or "[]")
        except (AudacityMCPError, ValueError, TypeError, AttributeError):
            return []
        if not isinstance(tracks, list) or track >= len(tracks):
            return []
        actual = tracks[track]
        if not isinstance(actual, dict):
            return []
        # GetInfo answers with lowercase keys ("mute", "solo") while
        # SetTrackStatus takes capitalised ones, so match case-insensitively.
        lowered = {str(k).lower(): v for k, v in actual.items()}
        ignored = []
        for flag, wanted in expected.items():
            if flag.lower() not in lowered:
                continue
            if bool(lowered[flag.lower()]) != bool(wanted):
                ignored.append(flag)
        if not ignored:
            return []
        return [
            f"Audacity reported success but {' and '.join(ignored)} did not change on track "
            f"{track}. SetTrackStatus silently ignores these flags; set them in the Audacity "
            "UI, or use track gain instead of mute."
        ]

    @mcp.tool()
    async def track_add_mono() -> dict:
        """Add a new mono audio track to the project."""
        return await client.execute("NewMonoTrack")

    @mcp.tool()
    async def track_add_stereo() -> dict:
        """Add a new stereo audio track to the project."""
        return await client.execute("NewStereoTrack")

    @mcp.tool()
    async def track_remove() -> dict:
        """Remove the currently selected track(s). Select tracks first with track_select."""
        return await client.execute("RemoveTracks")

    @mcp.tool()
    async def track_set_properties(
        track: int,
        name: str | None = None,
        gain: float | None = None,
        pan: float | None = None,
        mute: bool | None = None,
        solo: bool | None = None,
    ) -> dict:
        """Set properties of a track by index.

        Args:
            track: Track index (0-based)
            name: New track name
            gain: Track gain in dB (-36 to 36)
            pan: Track pan (-1.0=left to 1.0=right)
            mute: Mute the track
            solo: Solo the track

        Mute and solo are read back after the write, because Audacity accepts
        them and does nothing. When that happens the result carries a warning
        naming the flags that did not change.
        """
        if track < 0 or track >= MAX_TRACKS:
            raise AudacityMCPError(ErrorCode.VALUE_OUT_OF_RANGE, f"Track index must be 0-{MAX_TRACKS - 1}")
        params: dict = {"Track": track}
        if name is not None:
            params["Name"] = name
        if gain is not None:
            if not -36 <= gain <= 36:
                raise AudacityMCPError(ErrorCode.VALUE_OUT_OF_RANGE, "Gain must be -36 to 36 dB")
            params["Gain"] = gain
        if pan is not None:
            if not -1.0 <= pan <= 1.0:
                raise AudacityMCPError(ErrorCode.VALUE_OUT_OF_RANGE, "Pan must be -1.0 to 1.0")
            params["Pan"] = pan
        expected = {}
        if mute is not None:
            params["Mute"] = mute
            expected["Mute"] = mute
        if solo is not None:
            params["Solo"] = solo
            expected["Solo"] = solo
        result = await client.execute("SetTrackStatus", **params)
        warnings = await _verify_track_flags(track, expected)
        if warnings:
            result["warnings"] = warnings
        return result

    @mcp.tool()
    async def track_get_info() -> dict:
        """Get information about all tracks in the project (names, types, rates, etc.)."""
        return await client.execute("GetInfo", Type="Tracks")

    @mcp.tool()
    async def track_mix_and_render() -> dict:
        """Mix and render selected tracks into a single track. Select tracks first."""
        return await client.execute("MixAndRender")

    @mcp.tool()
    async def track_mute(track: int, mute: bool = True) -> dict:
        """Mute or unmute a track.

        Audacity 3.7.8 accepts this and does nothing: SetTrackStatus reports
        success and the mute flag stays where it was. The result is read back
        and carries a warning when that happens. To actually silence a track,
        set its gain to the minimum, or ask the user to click Mute.

        Args:
            track: Track index (0-based)
            mute: True to mute, False to unmute
        """
        if track < 0 or track >= MAX_TRACKS:
            raise AudacityMCPError(ErrorCode.VALUE_OUT_OF_RANGE, f"Track index must be 0-{MAX_TRACKS - 1}")
        result = await client.execute("SetTrackStatus", Track=track, Mute=mute)
        warnings = await _verify_track_flags(track, {"Mute": mute})
        if warnings:
            result["warnings"] = warnings
        return result

    @mcp.tool()
    async def track_select(track: int) -> dict:
        """Select a track by index. Many operations require selecting a track first.

        Args:
            track: Track index (0-based)
        """
        if track < 0 or track >= MAX_TRACKS:
            raise AudacityMCPError(ErrorCode.VALUE_OUT_OF_RANGE, f"Track index must be 0-{MAX_TRACKS - 1}")
        return await client.execute("SelectTracks", Track=track, TrackCount=1)

    @mcp.tool()
    async def track_add_label() -> dict:
        """Add a new empty label track to the project."""
        return await client.execute("NewLabelTrack")

    @mcp.tool()
    async def track_stereo_to_mono() -> dict:
        """Convert the selected stereo track to mono. Select the track first."""
        return await client.execute_long("StereoToMono")

    @mcp.tool()
    async def track_mix_and_render_to_new() -> dict:
        """Mix and render selected tracks into a new track, keeping the originals. Select tracks first."""
        return await client.execute_long("MixAndRenderToNewTrack")

    @mcp.tool()
    async def track_mute_all() -> dict:
        """Mute all tracks in the project."""
        return await client.execute("MuteAllTracks")

    @mcp.tool()
    async def track_unmute_all() -> dict:
        """Unmute all tracks in the project."""
        return await client.execute("UnmuteAllTracks")

    @mcp.tool()
    async def track_resample(rate: int = 44100) -> dict:
        """Resample the selected track to a new sample rate.

        Args:
            rate: Target sample rate in Hz (e.g. 44100, 48000, 96000). Must be > 0.
        """
        if not 1 <= rate <= 384000:
            raise AudacityMCPError(ErrorCode.VALUE_OUT_OF_RANGE, "rate must be 1-384000 Hz")
        result = await client.execute("Resample", Rate=rate)
        if rate not in COMMON_SAMPLE_RATES:
            result["warning"] = (
                f"{rate} Hz is not a rate most sound devices can play back. "
                "If Audacity later reports 'Error opening sound device', this is why."
            )
        return result

    @mcp.tool()
    async def track_align_end_to_end() -> dict:
        """Align selected tracks end-to-end (sequentially). Select the tracks first."""
        return await client.execute("Align_EndToEnd")
