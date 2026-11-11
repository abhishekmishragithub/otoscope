"""pytest fixture and opt-in gate. Reports intentionally omit exception messages/URIs."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from .mock import MockTransport
from .report import write_report
from .session import CallSession
from .sipling import SiplingTransport
from .transport import Transport

_RESULTS: pytest.StashKey[list[dict[str, Any]]] = pytest.StashKey()
_OUTCOMES: pytest.StashKey[dict[str, str]] = pytest.StashKey()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("otoscope")
    group.addoption(
        "--otoscope-live",
        action="store_true",
        dest="otoscope_live",
        help="Allow real calls (may cost money); requires consent",
    )
    group.addoption("--otoscope-report", default=".otoscope", help="Local artifact directory")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: real call requiring explicit opt-in")
    config.stash[_RESULTS] = []
    config.stash[_OUTCOMES] = {}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("otoscope_live"):
        for item in items:
            fixtures = item.fixturenames if isinstance(item, pytest.Function) else ()
            if item.get_closest_marker("live") and not (
                "sip_caller" in fixtures and "otoscope_transport" not in fixtures
            ):
                item.add_marker(pytest.mark.skip(reason="requires --otoscope-live"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Iterator[None]:
    yield
    # Avoid retaining exception values, which can expose dial identities.
    if call.when == "setup" and call.excinfo is not None:
        item.config.stash[_OUTCOMES][item.nodeid] = "setup failed or skipped"
    elif call.when == "call":
        item.config.stash[_OUTCOMES][item.nodeid] = "failed" if call.excinfo else "passed"
    elif call.when == "teardown" and call.excinfo:
        item.config.stash[_OUTCOMES][item.nodeid] = "teardown failed"
        for result in item.config.stash[_RESULTS]:
            if result["artifact_id"] == hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]:
                result["outcome"] = "teardown failed"


@pytest.fixture
def otoscope_transport(request: pytest.FixtureRequest) -> Transport:
    """Override this fixture for platform-specific adapters or mock scripts."""
    if request.node.get_closest_marker("live"):
        return SiplingTransport()
    return MockTransport()


@pytest.fixture
def otoscope_target(request: pytest.FixtureRequest) -> str:
    if not request.config.getoption("otoscope_live"):
        pytest.skip("requires --otoscope-live")
    value = os.environ.get("OTOSCOPE_TARGET_URI")
    if not value:
        pytest.skip("set OTOSCOPE_TARGET_URI")
    return value


@pytest.fixture
def call(request: pytest.FixtureRequest, otoscope_transport: Transport) -> Iterator[CallSession]:
    session = CallSession(otoscope_transport)
    try:
        yield session
    finally:
        session.close()
        if session.handle:
            directory = Path(request.config.getoption("otoscope_report"))
            identity = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]
            evidence = session.artifacts(directory / identity)
            # Parametrized node names may contain SIP identities. Drop all parameter text.
            name = re.sub(r"\[.*\]", "[redacted]", request.node.nodeid)
            evidence.update(
                test=name,
                artifact_id=identity,
                outcome=request.config.stash[_OUTCOMES].get(request.node.nodeid, "incomplete"),
            )
            request.config.stash[_RESULTS].append(evidence)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    results = session.config.stash[_RESULTS]
    if results:
        write_report(Path(session.config.getoption("otoscope_report")), results)
