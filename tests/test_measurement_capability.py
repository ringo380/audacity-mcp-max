import audacity_mcp.measurement as m


class TestCapabilityProbe:
    def test_reports_all_three_flags(self):
        cap = m.describe_capability()
        assert set(cap) == {"numpy", "scipy", "loudness", "reason"}
        assert isinstance(cap["numpy"], bool)
        assert isinstance(cap["scipy"], bool)

    def test_loudness_requires_both(self):
        assert m.LOUDNESS_AVAILABLE == (m.HAVE_NUMPY and m.HAVE_SCIPY)

    def test_reason_is_empty_when_available(self):
        cap = m.describe_capability()
        if cap["loudness"]:
            assert cap["reason"] == ""

    def test_reason_names_the_install_command_when_unavailable(self, monkeypatch):
        """A caller that cannot measure loudness must be told how to fix it,
        not just that it failed."""
        monkeypatch.setattr(m, "HAVE_NUMPY", False)
        monkeypatch.setattr(m, "LOUDNESS_AVAILABLE", False)
        cap = m.describe_capability()
        assert cap["loudness"] is False
        assert "measurement" in cap["reason"]
        assert "numpy" in cap["reason"]
