import { forwardRef } from "react";
import { cn } from "../../lib/utils.js";

/**
 * 表单原语。
 *
 * 外观统一走 styles.css 里的 ``.field-input``（深色皮肤 + 交互态），
 * 这里只负责结构与尺寸，避免每个页面重复写一长串工具类。
 */

export function Field({ label, hint, className, children }) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <span className="text-xs text-muted">{label}</span>
      {children}
      {hint && <span className="text-xs text-faint">{hint}</span>}
    </label>
  );
}

/** 复选框行：勾选控件与文案同一行，用于能力开关 / 开关项。 */
export function CheckboxField({ className, children, title, ...props }) {
  return (
    <label
      className={cn("inline-flex cursor-pointer items-center gap-2 text-sm text-fg2", className)}
      title={title}
    >
      <Checkbox {...props} />
      {children}
    </label>
  );
}

export const Input = forwardRef(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn("field-input", className)} {...props} />;
});

export const Select = forwardRef(function Select({ className, children, ...props }, ref) {
  return (
    <select ref={ref} className={cn("field-input cursor-pointer", className)} {...props}>
      {children}
    </select>
  );
});

export const Textarea = forwardRef(function Textarea({ className, ...props }, ref) {
  return <textarea ref={ref} className={cn("field-input resize-y", className)} {...props} />;
});

export const Checkbox = forwardRef(function Checkbox({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      type="checkbox"
      className={cn("h-[15px] w-[15px] shrink-0 cursor-pointer accent-accent", className)}
      {...props}
    />
  );
});

/** 表单区块的小标题（"能力""成本""高级配置项"…）。 */
export function FieldLabel({ className, children }) {
  return (
    <p className={cn("mb-2 mt-4 text-xs tracking-wide text-muted", className)}>{children}</p>
  );
}

/** 表单行：等宽自适应换行，列宽由内部 Field 的 min-width 决定。 */
export function FormRow({ className, children }) {
  return <div className={cn("flex flex-wrap items-start gap-3", className)}>{children}</div>;
}
