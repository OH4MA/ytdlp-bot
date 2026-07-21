"""Process supervision port (controller-side abstraction)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class ProcessHandle(Protocol):
    @property
    def pid(self) -> int: ...

    async def wait(self) -> int: ...

    async def terminate_group(self) -> None: ...

    async def kill_group(self) -> None: ...


class ProcessLauncher(Protocol):
    async def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: dict[str, str],
        new_session: bool = True,
    ) -> ProcessHandle: ...
