"""查询接口：把 storage 的原始查询包成 Web 友好的结构。

storage 提供原子查询，这里负责组合与视图——Dashboard 需要的"趋势"是
多个时间窗样本，Trace 搜索需要结果里带上整条链路。这一层只做纯组合，
本身不碰数据库。
"""

from __future__ import annotations

from .storage import Storage

__all__ = ["Query"]

#: Dashboard 趋势采样的时间窗（秒）。从近到远。
TREND_WINDOWS: tuple[int, ...] = (60, 3600, 86400)


class Query:
    """Metrics / Logs / Trace 的高层查询视图。"""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    async def dashboard(self) -> dict[str, object]:
        """Dashboard 聚合数据。

        返回单页所需的全部指标与各模型健康状态，供 Web 一次拉取。
        """
        health = await self.storage.model_health()
        return {
            "qps_1m": await self.storage.qps(60),
            "qps_1h": await self.storage.qps(3600),
            "error_rate_1m": await self.storage.error_rate(60),
            "p99_latency_1m": await self.storage.p99_latency(60),
            "total_cost_1h": await self.storage.total_cost(3600),
            "health": health,
        }

    async def trend(self) -> dict[str, list[dict[str, float]]]:
        """三个时间窗的指标样本，供 Dashboard 画趋势线。"""
        out: dict[str, list[dict[str, float]]] = {"qps": [], "error_rate": [], "p99_latency": []}
        for window in TREND_WINDOWS:
            out["qps"].append({"window_seconds": window, "value": await self.storage.qps(window)})
            out["error_rate"].append(
                {"window_seconds": window, "value": await self.storage.error_rate(window)}
            )
            out["p99_latency"].append(
                {"window_seconds": window, "value": await self.storage.p99_latency(window)}
            )
        return out

    async def trace(self, trace_id: str) -> dict[str, object]:
        """按 trace_id 返回链路与其中的调用明细。"""
        spans = await self.storage.trace(trace_id)
        return {"trace_id": trace_id, "calls": spans}