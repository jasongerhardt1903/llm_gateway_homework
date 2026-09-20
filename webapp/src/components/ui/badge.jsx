import { cn } from "../../lib/utils.js";

/** 状态标签：终态 / 可用性 / 能力等短枚举统一用它，避免各页各写一套配色。 */
const TONES = {
  ok: "border-ok/35 bg-ok/12 text-ok",
  bad: "border-bad/35 bg-bad/12 text-bad",
  warn: "border-warn/35 bg-warn/15 text-warn",
  accent: "border-accent/35 bg-accent/12 text-accent",
  neutral: "border-border-strong bg-panel2 text-muted",
};

export function Badge({ tone = "neutral", className, children }) {
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-full border px-2 py-px text-xs leading-5",
        TONES[tone] ?? TONES.neutral,
        className
      )}
    >
      {children}
    </span>
  );
}
