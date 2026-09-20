import { cn } from "../../lib/utils.js";

export function Card({ className, children, ...props }) {
  return (
    <section
      className={cn("rounded-lg border border-border bg-panel p-4", className)}
      {...props}
    >
      {children}
    </section>
  );
}

export function CardHeader({ className, children }) {
  return (
    <div className={cn("mb-3 flex flex-wrap items-baseline justify-between gap-2", className)}>
      {children}
    </div>
  );
}

export function CardTitle({ className, children }) {
  return <h3 className={cn("text-sm font-semibold text-fg", className)}>{children}</h3>;
}

export function CardDescription({ className, children }) {
  return <p className={cn("text-xs text-muted", className)}>{children}</p>;
}

/** 指标卡：Dashboard 顶部的 QPS / 错误率 / P99 / 成本。 */
export function StatCard({ label, value, hint, icon: Icon, tone = "accent" }) {
  const tones = {
    accent: "text-accent",
    ok: "text-ok",
    bad: "text-bad",
    warn: "text-warn",
  };
  return (
    <Card className="transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-3">
        <div className="text-xs text-muted">{label}</div>
        {Icon && <Icon size={15} className={cn("shrink-0", tones[tone] ?? tones.accent)} />}
      </div>
      <div className="tabular mt-2 text-[22px] font-semibold leading-tight tracking-tight text-fg">
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-faint">{hint}</div>}
    </Card>
  );
}
