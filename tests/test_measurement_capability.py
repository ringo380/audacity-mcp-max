import pytest

import audacity_mcp.measurement as m


@pytest.fixture
def flags(monkeypatch):
    """Set the two probe results, whatever this install actually has.

    Without this every assertion here is conditional on the machine running
    the suite: on a full install the unavailable paths never execute, and on a
    bare install the available ones do not. Both are shipped code.
    """
    def _set(numpy: bool, scipy: bool):
        monkeypatch.setattr(m, "HAVE_NUMPY", numpy)
        monkeypatch.setattr(m, "HAVE_SCIPY", scipy)
    return _set


class TestCapabilityProbe:
    def test_reports_all_three_flags(self):
        cap = m.describe_capability()
        assert set(cap) == {"numpy", "scipy", "loudness", "reason"}
        assert isinstance(cap["numpy"], bool)
        assert isinstance(cap["scipy"], bool)

    @pytest.mark.parametrize(
        "numpy,scipy,loudness",
        [(True, True, True), (True, False, False),
         (False, True, False), (False, False, False)],
    )
    def test_loudness_requires_both(self, flags, numpy, scipy, loudness):
        """K-weighting is an IIR filter, so numpy alone leaves it a per-sample
        Python loop - tens of minutes on an hour of audio. Both or nothing.

        Driven over all four combinations rather than asserted as
        `LOUDNESS_AVAILABLE == (HAVE_NUMPY and HAVE_SCIPY)`, which restated the
        implementation line against itself: on a machine with both extras
        installed, that comparison passes just as well if the `and` becomes an
        `or`, because both sides are True either way.
        """
        flags(numpy, scipy)
        assert m.describe_capability()["loudness"] is loudness

    def test_reason_is_empty_when_available(self, flags):
        """Unconditional. This used to be guarded by `if cap["loudness"]:`, so
        on any install without the extra it asserted nothing at all - and the
        no-extra run is half of what this project's gate checks."""
        flags(True, True)
        assert m.describe_capability()["reason"] == ""

    @pytest.mark.parametrize(
        "numpy,scipy,named,not_named",
        [(False, True, ("numpy",), ("scipy",)),
         (True, False, ("scipy",), ("numpy",)),
         (False, False, ("numpy", "scipy"), ())],
    )
    def test_the_reason_names_exactly_what_is_missing(
        self, flags, numpy, scipy, named, not_named
    ):
        """A caller that cannot measure loudness must be told which piece is
        missing and how to fix it. Naming a package that is present sends them
        after the wrong thing."""
        flags(numpy, scipy)
        reason = m.describe_capability()["reason"]
        for name in named:
            assert name in reason
        for name in not_named:
            assert name not in reason
        assert "measurement" in reason, "the reason must name the install fix"

    def test_the_flags_report_what_was_probed(self, flags):
        flags(True, False)
        cap = m.describe_capability()
        assert cap["numpy"] is True
        assert cap["scipy"] is False

    def test_the_constant_is_false_when_only_one_half_is_present(self, monkeypatch):
        """`LOUDNESS_AVAILABLE = HAVE_NUMPY and HAVE_SCIPY` is computed once at
        import, so no amount of monkeypatching the flags re-runs it. On a
        machine with both extras it reads True whether the operator is `and` or
        `or`, and on a machine with neither it reads False either way - the two
        configurations the gate actually runs. Only exactly-one-present tells
        them apart, so this test builds that by reloading the module with scipy
        unimportable.
        """
        import builtins
        import importlib

        if not (m.HAVE_NUMPY and m.HAVE_SCIPY):
            pytest.skip("needs both extras installed to hide one of them")

        real_import = builtins.__import__

        def _no_scipy(name, *args, **kwargs):
            if name.split(".")[0] == "scipy":
                raise ImportError("hidden by the test")
            return real_import(name, *args, **kwargs)

        try:
            monkeypatch.setattr(builtins, "__import__", _no_scipy)
            reloaded = importlib.reload(m)
            assert reloaded.HAVE_NUMPY is True
            assert reloaded.HAVE_SCIPY is False
            assert reloaded.LOUDNESS_AVAILABLE is False
        finally:
            # Restore the module for every later test in the process: the
            # reload above rebound this module's globals for real.
            monkeypatch.undo()
            importlib.reload(m)

        assert m.HAVE_SCIPY is True
        assert m.LOUDNESS_AVAILABLE is True

    def test_the_module_constant_agrees_with_the_probe(self):
        """LOUDNESS_AVAILABLE is imported directly by report.py, so it has to
        mean the same thing as the flag describe_capability reports on a real
        install - the one case the monkeypatched tests above cannot see."""
        assert m.LOUDNESS_AVAILABLE is m.describe_capability()["loudness"]
