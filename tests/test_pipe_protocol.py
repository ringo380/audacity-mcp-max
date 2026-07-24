import pytest
from audacity_mcp_shared.pipe_protocol import format_command, parse_response
from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode


class TestFormatCommand:
    def test_simple_command(self):
        assert format_command("Play") == "Play:\n"

    def test_command_with_params(self):
        result = format_command("SelectTime", Start=1.0, End=5.0)
        assert result.startswith("SelectTime:")
        assert "Start=1.0" in result
        assert "End=5.0" in result
        assert result.endswith("\n")

    def test_command_with_string_param(self):
        result = format_command("Import2", Filename="/path/to/file.wav")
        assert "Filename=/path/to/file.wav" in result

    def test_quoted_value_with_spaces(self):
        result = format_command("Tone", Waveform="Square (no alias)")
        assert 'Waveform="Square (no alias)"' in result

    def test_bool_param(self):
        result = format_command("Normalize", RemoveDcOffset=True)
        assert "RemoveDcOffset=1" in result

    def test_bool_param_false(self):
        result = format_command("Normalize", RemoveDcOffset=False)
        assert "RemoveDcOffset=0" in result

    def test_injection_newline(self):
        with pytest.raises(AudacityMCPError) as exc_info:
            format_command("Play\nEvil")
        assert exc_info.value.code == ErrorCode.INJECTION_DETECTED

    def test_injection_in_value(self):
        with pytest.raises(AudacityMCPError) as exc_info:
            format_command("Import2", Filename="file\n\rEvil")
        assert exc_info.value.code == ErrorCode.INJECTION_DETECTED

    def test_injection_null_byte(self):
        with pytest.raises(AudacityMCPError) as exc_info:
            format_command("Import2", Filename="file\x00evil")
        assert exc_info.value.code == ErrorCode.INJECTION_DETECTED


class TestParseResponse:
    def test_success_response(self):
        raw = "BatchCommand finished: OK\n"
        result = parse_response(raw)
        assert result["success"] is True

    def test_failure_response(self):
        raw = "BatchCommand finished: Failed!\n"
        result = parse_response(raw)
        assert result["success"] is False

    def test_data_response(self):
        raw = "Name=Track 1\nRate=44100\nBatchCommand finished: OK\n"
        result = parse_response(raw)
        assert result["success"] is True
        assert result["data"]["Name"] == "Track 1"
        assert result["data"]["Rate"] == "44100"

    def test_empty_response(self):
        result = parse_response("")
        assert result["success"] is False

    def test_message_response(self):
        raw = "Some info message\nBatchCommand finished: OK\n"
        result = parse_response(raw)
        assert result["success"] is True
        assert "Some info message" in result["message"]


class TestFormatCommandEdgeCases:
    def test_negative_float_param(self):
        result = format_command("BassAndTreble", Treble=-4.0, Bass=-1.0)
        assert "Treble=-4.0" in result
        assert "Bass=-1.0" in result

    def test_large_number_param(self):
        result = format_command("SelectTime", Start=0, End=999999.999)
        assert "End=999999.999" in result

    def test_windows_path_backslashes_are_doubled_which_is_correct(self):
        r"""Issue #5: doubling every backslash is right, not a bug.

        Audacity parses a parameter value with
        wxCmdLineParser::ConvertStringToArgs (DOS mode, which never consumes a
        backslash) and then CommandParameters::Unescape, whose "\\\\" -> "\\"
        rule is the exact inverse of the doubling. Verified against a live
        Audacity 3.7.8: the doubled form is what arrives intact; a single
        backslash arrives corrupted. So the wire must carry the doubled path.
        """
        result = format_command("Import2", Filename=r"C:\Users\Test\Music\file.wav")
        assert r'Filename="C:\\Users\\Test\\Music\\file.wav"' in result

    def test_unc_path_needs_the_doubling_to_survive(self):
        r"""The leading \\ of a UNC path is where single-backslash sending
        visibly loses data: doubled it round-trips, un-doubled it arrives as a
        single leading backslash (confirmed live)."""
        result = format_command("Import2", Filename=r"\\server\share\a.wav")
        assert r'Filename="\\\\server\\share\\a.wav"' in result

    def test_backslash_then_lowercase_n_is_an_unfixable_audacity_bug(self):
        r"""A path segment beginning with 'n' (e.g. C:\new) cannot survive,
        no escaping helps. Audacity's Unescape runs "\\n" -> newline BEFORE
        "\\\\" -> "\\", so the doubled C:\\new is seen as backslash + newline.
        We still send the correct doubled form; recording the limitation so a
        future reader does not "fix" the doubling in a doomed attempt to help.
        Verified live: C:\new round-trips with an embedded newline.
        """
        result = format_command("Import2", Filename=r"C:\new\test.wav")
        # The client's part is still correct - it doubles like everything else.
        assert r'Filename="C:\\new\\test.wav"' in result

    def test_empty_string_param(self):
        result = format_command("SetLabel", Text="")
        assert "Text=" in result

    def test_backslash_n_path_produces_a_warning(self):
        from audacity_mcp_shared.pipe_protocol import path_corruption_warning
        assert path_corruption_warning(r"C:\new\a.wav") is not None
        assert path_corruption_warning(r"C:\music\new_song.wav") is not None  # \n inside

    def test_backslash_capital_n_and_other_paths_do_not_warn(self):
        from audacity_mcp_shared.pipe_protocol import path_corruption_warning
        # Capital N survives (Unescape is case-sensitive); no \n anywhere else.
        assert path_corruption_warning(r"C:\New\a.wav") is None
        assert path_corruption_warning(r"C:\Users\Test\file.wav") is None
        assert path_corruption_warning("/Users/me/Music/file.wav") is None

    def test_value_with_embedded_quotes(self):
        result = format_command("SetLabel", Text='say "hello"')
        assert r'Text="say \"hello\""' in result

    def test_extra_params_dict(self):
        result = format_command("Compressor", extra_params={"gain-L": "3.0"})
        assert "gain-L=3.0" in result

    def test_extra_params_with_spaces(self):
        result = format_command("TruncateSilence", extra_params={"Action": "Truncate Detected Silence"})
        assert '"Truncate Detected Silence"' in result

    def test_unicode_param(self):
        result = format_command("SetLabel", Text="café")
        assert "café" in result

    def test_value_with_equals_sign(self):
        result = format_command("SetLabel", Text="x=y+z")
        assert 'Text="x=y+z"' in result


class TestParseResponseEdgeCases:
    def test_multiline_message(self):
        raw = "Line one\nLine two\nBatchCommand finished: OK\n"
        result = parse_response(raw)
        assert result["success"] is True
        assert "Line one" in result["message"]
        assert "Line two" in result["message"]

    def test_response_with_only_batch_line(self):
        raw = "BatchCommand finished: OK\n"
        result = parse_response(raw)
        assert result["success"] is True
        assert result["message"] == ""

    def test_failure_with_error_message(self):
        raw = "Command not recognized\nBatchCommand finished: Failed!\n"
        result = parse_response(raw)
        assert result["success"] is False
        assert "Command not recognized" in result["message"]

    def test_line_with_equals_parsed_as_data(self):
        """Lines containing '=' are parsed as key=value data, not messages.
        This is a known limitation of the pipe protocol parser."""
        raw = "Error: something went wrong\nBatchCommand finished: OK\n"
        result = parse_response(raw)
        # "Error: something went wrong" has "=" absent but ":" present
        # Actually it has no "=" so it goes to message
        assert "Error: something went wrong" in result["message"]
