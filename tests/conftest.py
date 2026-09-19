"""pytest 共享配置。

把仓库根加入 ``sys.path``，使 ``import llm_gw`` 与 ``tests.support`` 在未安装
包的情况下也可用（TDD 迭代更轻量）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def clock():
    """默认注入 FakeClock：任何使用它的测试都不会真实等待。"""
    from llm_gw.util.clock import FakeClock

    return FakeClock()
