"""AC13 access modes and AC15 non-admin denial."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes import (
    InMemoryAccessRepository,
    InMemoryArtifactRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.domain.enums import AccessMode, Platform
from ytdlp_bot.domain.errors import AuthorizationError
from ytdlp_bot.domain.identity import Identity


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac13_allow_all_and_whitelist() -> None:
    user = Identity(platform=Platform.TELEGRAM, user_id="1")
    admin = Identity(platform=Platform.TELEGRAM, user_id="99")
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()

    allow = InMemoryAccessRepository(AccessMode.ALLOW_ALL)
    auth_allow = AuthorizationService(
        access=allow, jobs=jobs, artifacts=arts, administrators=frozenset({admin})
    )
    await auth_allow.require_user_access(user)

    white = InMemoryAccessRepository(AccessMode.WHITELIST)
    auth_white = AuthorizationService(
        access=white, jobs=jobs, artifacts=arts, administrators=frozenset({admin})
    )
    with pytest.raises(AuthorizationError):
        await auth_white.require_user_access(user)
    await white.add_identity(user, now=datetime.now(UTC))
    await auth_white.require_user_access(user)
    # Admin always allowed for admin ops.
    await auth_white.require_administrator(admin)
    with pytest.raises(AuthorizationError):
        await auth_white.require_administrator(user)
