import { useEffect, useState } from "react";
import { getDashboard } from "../api.js";

/**
 * Dashboard 页。
 *
 * 上半部分是网关整体指标（QPS / 错误率 / P99 / 成本），下半部分是需求要求的
 * "所有已配置大模型的可用状态、使用次数、使用 token 数、剩余 token 数"。
 */
export default function DashboardPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = () =>
    getDashboard()
      .then((payload) => {
        setData(payload);
        setError("");
      })
      .catch((err) => setError(err.message));

  useEffect(() => {
    load();
    // 指标是滚动时间窗，定时刷新比手动点更贴近"看板"用法。
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  if (error) {
    return (
      <div className="page">
        <h2>Dashboard</h2>
        <div className="banner banner-error">{error}</div>
      </div>
    );
  }
  if (!data) return <div className="page"><p className="muted">加载中…</p></div>;

  return (
    <div className="page">
      <h2>Dashboard</h2>

      <div className="cards">
        <Metric label="QPS（1 分钟）" value={round(data.qps_1m, 3)} />
        <Metric label="错误率（1 分钟）" value={percent(data.error_rate_1m)} />
        <Metric label="P99 延迟（1 分钟）" value={`${round(data.p99_latency_1m, 1)} ms`} />
        <Metric label="累计成本（1 小时）" value={`$${round(data.total_cost_1h, 6)}`} />
      </div>

      <section>
        <h3>模型状态</h3>
        <table className="grid">
          <thead>
            <tr>
              <th>模型</th>
              <th>供应商</th>
              <th>可用状态</th>
              <th>成功 / 失败</th>
              <th>使用次数</th>
              <th>已用 token</th>
              <th>剩余 token</th>
              <th>最近错误</th>
            </tr>
          </thead>
          <tbody>
            {(data.models ?? []).map((row) => (
              <tr key={`${row.provider}/${row.model}`}>
                <td>{row.model}</td>
                <td>{row.display_provider || row.provider}</td>
                <td>
                  <span className={row.available ? "pill pill-ok" : "pill pill-bad"}>
                    {row.available ? "可用" : "不可用"}
                  </span>
                </td>
                <td>
                  {row.success_count} / {row.error_count}
                </td>
                <td>{row.success_count + row.error_count}</td>
                <td>{row.used_tokens}</td>
                <td>{row.remaining_tokens === null ? "—" : row.remaining_tokens}</td>
                <td className="muted">{row.last_error || "—"}</td>
              </tr>
            ))}
            {(data.models ?? []).length === 0 && (
              <tr>
                <td colSpan="8" className="muted">
                  尚未配置模型。
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <p className="muted">
          未配置配额的模型"剩余 token"显示为「—」；"使用次数"按成功 + 失败的调用总数统计。
        </p>
      </section>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className="card-value">{value}</div>
    </div>
  );
}

const round = (value, digits) => Number(value ?? 0).toFixed(digits);
const percent = (value) => `${(Number(value ?? 0) * 100).toFixed(2)}%`;