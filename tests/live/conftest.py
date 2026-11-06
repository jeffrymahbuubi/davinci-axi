"""Live-test guard (R37): real Resolve, and only when the open project is `davinci-axi-test`."""

from __future__ import annotations

import pytest

from davinci_axi.connect import connect
from davinci_axi.session import Session
from davinci_axi.spec import GlobalOpts

TEST_PROJECT = "davinci-axi-test"


@pytest.fixture
def live_session(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Connect to the running Resolve, but only if the current project is the throwaway one.

    The root conftest forces `DAVINCI_AXI_FAKE=1` for every test, so it is removed here. A wrong
    or missing project is a hard failure, never a skip: a skip would hide that the guard fired.
    """
    if not request.config.getoption("--live"):
        pytest.skip("needs --live and a running DaVinci Resolve")
    monkeypatch.delenv("DAVINCI_AXI_FAKE", raising=False)
    session = connect(GlobalOpts())
    project = session.current_project()
    name = project.GetName() if project is not None else None
    if name != TEST_PROJECT:
        pytest.fail(
            f"live tests only run in the project {TEST_PROJECT!r}, but the current project is "
            f"{name!r}. Open {TEST_PROJECT!r} in Resolve (create an empty one if needed) and "
            "re-run. Nothing was changed.",
            pytrace=False,
        )
    return session
