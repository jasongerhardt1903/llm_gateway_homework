"""时间与睡眠的可注入抽象。

重试逻辑必须能够被测试驱动而**不产生真实等待**，因此所有 sleep 都通过
:class:`Clock` 注入。测试注入 :class:`FakeClock` 并断言 ``sleeps`` 列表，
生产环境注入 :class:`RealClock`。
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    """时间源与睡眠的抽象。"""

    def now(self) -> float:
        """返回当前时间（秒，单调递增，用于计算延迟）。"""
        ...

    async def sleep(self, ms: float) -> None:
        """睡眠指定毫秒数。"""
        ...


class RealClock:
    """生产环境时钟：真实睡眠。"""

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, ms: float) -> None:
        if ms > 0:
            await asyncio.sleep(ms / 1000.0)


class FakeClock:
    """测试时钟：记录睡眠时长并立即返回，永不真实等待。

    ``sleeps`` 保存每次请求的睡眠毫秒数，测试据此断言退避序列，
    例如 ``assert clock.sleeps == [500, 1000]``。
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._now

    async def sleep(self, ms: float) -> None:
        self.sleeps.append(ms)
        # 时间随睡眠推进，使 now() 与真实行为一致。
        self._now += max(0.0, ms) / 1000.0
