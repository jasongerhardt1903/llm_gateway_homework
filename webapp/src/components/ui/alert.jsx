import { cn } from "../../lib/utils.js";

/** 结果横幅：错误 / 成功 / 提示。 */
const TONES = {
  error: "border-bad/40 bg-bad/12 text-[#ffb4ae]",
  ok: "border-ok/40 bg-ok/12 text-[#97e6a0]",
  info: "border-accent/40 bg-accent/12 text-accent",
};

export function Alert({ tone = "error", className, children }) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn("rounded-lg border px-3 py-2.5 text-sm leading-relaxed", TONES[tone], className)}
    >
      {children}
    </div>
  );
}
