"""NET readiness requires egress; admission closed when not ready."""

from __future__ import annotations

import pytest

from ytdlp_bot.adapters.http.health import HealthController, ReadinessState


@pytest.mark.unit
def test_readyz_requires_egress_and_admission() -> None:
    state = ReadinessState(
        configuration=True,
        database=True,
        migrations=True,
        recovery=True,
        http=True,
        dispatcher=True,
        platforms=True,
        egress=False,
        storage=True,
        admission_open=True,
    )
    ctl = HealthController(readiness=state)
    status, body = ctl.ready()
    assert status == 503
    assert body.get("ready") is False
    deps = body.get("dependencies")
    assert isinstance(deps, dict)
    assert deps.get("egress") is False

    state.egress = True
    status, body = ctl.ready()
    assert status == 200
    assert body.get("ready") is True

    ctl.close_admission()
    status, body = ctl.ready()
    assert status == 503
