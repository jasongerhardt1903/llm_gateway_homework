import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Activity, DollarSign, Timer, TriangleAlert } from "lucide-react";
import { getDashboard } from "../api.js";
import { PageHeader } from "../components/ui/page-header.jsx";
import { Card, CardDescription, CardHeader, CardTitle, StatCard } from "../components/ui/card.jsx";
import { DataTable } from "../components/ui/data-table.jsx";
import { Alert } from "../components/ui/alert.jsx";
import { Badge } from "../components/ui/badge.jsx";

/**
 * Dashboard 页。
 *
 * 上半部分是网关整体指标（QPS / 错误率 / P99 / 成本），中间按模型给出调用量与
 * token 消耗的对比图，下半部分是需求要求的"所有已配置大模型的可用状态、
 * 使用次数、使用 token 数、剩余 token 数"。
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
      <div>
        <PageHeader title="Dashboard" description="网关整体指标与模型可用状态。" />
        <Alert>{error}</Alert>
      </div>
    );
  }
  if (!data) {
    return (
      <div>
        <PageHeader title="Dashboard" description="网关整体指标与模型可用状态。" />
        <p className="text-sm text-muted">加载中…</p>
      </div>
    );
  }

  const models = data.models ?? [];
  const chartRows = models.map((row) => ({
    name: row.model,
    success: row.success_count ?? 0,
    error: row.error_count ?? 0,
    tokens: row.used_tokens ?? 0,
  }));

  const columns = [
    {
      accessorKey: "model",
      header: "模型",
      cell: ({ getValue }) => <span className="text-fg">{getValue()}</span>,
    },
    {
      id: "provider",
      header: "供应商",
      accessorFn: (row) => row.display_provider || row.provider,
    },
    {
      id: "available",
      header: "可用状态",
      accessorFn: (row) => (row.available ? 1 : 0),
      cell: ({ row }) => (
        <Badge tone={row.original.available ? "ok" : "bad"}>
          {row.original.available ? "可用" : "不可用"}
        </Badge>
      ),
    },
    {
      id: "result",
      header: "成功 / 失败",
      enableSorting: false,
      cell: ({ row }) => (
        <span className="tabular">
          <span className="text-ok">{row.original.success_count}</span>
          <span className="text-faint"> / </span>
          <span className="text-bad">{row.original.error_count}</span>
        </span>
      ),
    },
    {
      id: "calls",
      header: "使用次数",
      accessorFn: (row) => (row.success_count ?? 0) + (row.error_count ?? 0),
      cell: ({ getValue }) => <span className="tabular">{getValue()}</span>,
    },
    {
      accessorKey: "used_tokens",
      header: "已用 token",
      cell: ({ getValue }) => <span className="tabular">{getValue()}</span>,
    },
    {
      id: "remaining_tokens",
      header: "剩余 token",
      accessorFn: (row) => (row.remaining_tokens === null ? -1 : Number(row.remaining_tokens)),
      cell: ({ row }) => (
        <span className="tabular">
          {row.original.remaining_tokens === null ? (
            <span className="text-faint">—</span>
          ) : (
            row.original.remaining_tokens
          )}
        </span>
      ),
    },
    {
      id: "last_error",
      header: "最近错误",
      enableSorting: false,
      cell: ({ row }) =>
        row.original.last_error ? (
          <span className="text-[#ffb4ae]">{row.original.last_error}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="指标为滚动时间窗（1 分钟 / 1 小时），每 5 秒自动刷新。"
      />

      <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3">
        <StatCard label="QPS（1 分钟）" value={round(data.qps_1m, 3)} icon={Activity} />
        <StatCard
          label="错误率（1 分钟）"
          value={percent(data.error_rate_1m)}
          icon={TriangleAlert}
          tone={Number(data.error_rate_1m ?? 0) > 0 ? "bad" : "ok"}
        />
        <StatCard
          label="P99 延迟（1 分钟）"
          value={`${round(data.p99_latency_1m, 1)} ms`}
          icon={Timer}
          tone="warn"
        />
        <StatCard
          label="累计成本（1 小时）"
          value={`$${round(data.total_cost_1h, 6)}`}
          icon={DollarSign}
          tone="accent"
        />
      </div>

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>模型调用分布</CardTitle>
          <CardDescription>成功与失败次数按模型对比</CardDescription>
        </CardHeader>
        <ChartBlock>
          <BarChart data={chartRows} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="#262d38" vertical={false} />
            <XAxis dataKey="name" stroke="#8b95a5" fontSize={12} tickLine={false} />
            <YAxis stroke="#8b95a5" fontSize={12} tickLine={false} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#202736" }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="success" name="成功" fill="#3fb950" radius={[3, 3, 0, 0]} />
            <Bar dataKey="error" name="失败" fill="#f85149" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartBlock>
      </Card>

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>模型状态</CardTitle>
          <CardDescription>
            {models.length ? `共 ${models.length} 个模型，点表头可排序` : "尚未配置模型"}
          </CardDescription>
        </CardHeader>
        <DataTable
          columns={columns}
          data={models}
          getRowKey={(row) => `${row.provider}/${row.model}`}
          empty="尚未配置模型。"
        />
        <p className="mt-3 text-xs text-muted">
          未配置配额的模型"剩余 token"显示为「—」；"使用次数"按成功 + 失败的调用总数统计。
        </p>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Token 消耗</CardTitle>
          <CardDescription>按模型统计已使用的总 token</CardDescription>
        </CardHeader>
        <ChartBlock>
          <BarChart data={chartRows} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="#262d38" vertical={false} />
            <XAxis dataKey="name" stroke="#8b95a5" fontSize={12} tickLine={false} />
            <YAxis stroke="#8b95a5" fontSize={12} tickLine={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#202736" }} />
            <Bar dataKey="tokens" name="已用 token" fill="#4f8cff" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartBlock>
      </Card>
    </div>
  );
}

/** 图表统一高度与主题；ResponsiveContainer 需要父级有确定高度。 */
function ChartBlock({ children }) {
  return (
    <div style={{ width: "100%", height: 260 }}>
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

const TOOLTIP_STYLE = {
  background: "#1c222c",
  border: "1px solid #2e3642",
  borderRadius: 8,
  fontSize: 12,
  color: "#e7ebf1",
};

const round = (value, digits) => Number(value ?? 0).toFixed(digits);
const percent = (value) => `${(Number(value ?? 0) * 100).toFixed(2)}%`;
