import json

pytest_plugins = ["pytester"]


def test_plugin_gate_reports_and_junit(pytester):
    pytester.makepyfile("""
        import pytest
        def test_offline(call):
            call.dial('sip:password@private')
            assert call.answered_within(.5)
        @pytest.mark.live
        def test_live(call):
            raise AssertionError('must not execute')
    """)
    result = pytester.runpytest("--otoscope-report=artifacts", "--junitxml=junit.xml")
    result.assert_outcomes(passed=1, skipped=1)
    report = json.loads((pytester.path / "artifacts/run.json").read_text())
    assert report["tests"][0]["outcome"] == "passed"
    assert "password" not in json.dumps(report)
    assert (pytester.path / "artifacts/index.html").exists()
    assert (pytester.path / "junit.xml").exists()


def test_failed_scenario_retains_artifacts_and_redacts_parameters(pytester):
    pytester.makepyfile("""
        import pytest
        @pytest.mark.parametrize('identity', ['sip:credential@host'])
        def test_failure(call, identity):
            call.dial(identity)
            assert call.answered_within(.5)
            assert False
    """)
    result = pytester.runpytest("--otoscope-report=artifacts")
    result.assert_outcomes(failed=1)
    data = (pytester.path / "artifacts/run.json").read_text()
    assert "credential" not in data
    report = json.loads(data)["tests"][0]
    assert report["outcome"] == "failed"
    assert (pytester.path / "artifacts" / report["artifact_id"] / "agent.wav").exists()


def test_sipling_plugin_coexists_with_independent_live_gates(pytester):
    import pytest

    pytest.importorskip("sipling")
    pytester.makeconftest("""
        import pytest
        from otoscope.mock import MockTransport
        @pytest.fixture
        def otoscope_transport():
            return MockTransport()
    """)
    pytester.makepyfile("""
        import pytest
        @pytest.mark.live
        def test_otoscope(call):
            call.dial('sip:offline')
            assert call.answered_within(.5)
        @pytest.mark.live
        def test_sipling(sip_caller):
            assert callable(sip_caller)  # Do not place a call in this gate test.
    """)
    pytester.runpytest("-p", "no:sipling", "-p", "sipling.pytest_plugin").assert_outcomes(skipped=2)
    pytester.runpytest(
        "-p", "no:sipling", "-p", "sipling.pytest_plugin", "--otoscope-live"
    ).assert_outcomes(passed=1, skipped=1)
    pytester.runpytest("-p", "no:sipling", "-p", "sipling.pytest_plugin", "--live").assert_outcomes(
        passed=1, skipped=1
    )
