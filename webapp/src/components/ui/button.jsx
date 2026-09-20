import { forwardRef } from "react";
import { cn } from "../../lib/utils.js";

/** 三级按钮体系：primary（主操作）/ secondary（并列次要）/ ghost（行内）/ danger（破坏性）。 */
const VARIANTS = {
  primary: "border-transparent bg-accent text-white hover:bg-accent-hover",
  secondary:
    "border-border-strong bg-transparent text-fg2 hover:border-[#3a4452] hover:bg-hover hover:text-fg",
  ghost: "border-transparent bg-transparent text-fg2 hover:bg-hover hover:text-fg",
  danger:
    "border-bad/45 bg-transparent text-[#ff7b72] hover:border-bad hover:bg-bad/15 hover:text-[#ff8a82]",
};

const SIZES = {
  sm: "h-7 gap-1.5 rounded-md px-2.5 text-xs",
  md: "h-9 gap-2 rounded-md px-4 text-sm",
  icon: "h-8 w-8 rounded-md p-0",
};

export const Button = forwardRef(function Button(
  { className, variant = "primary", size = "md", type = "button", ...props },
  ref
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        "inline-flex shrink-0 cursor-pointer items-center justify-center border font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-hover focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        "disabled:pointer-events-none disabled:opacity-45",
        VARIANTS[variant] ?? VARIANTS.primary,
        SIZES[size] ?? SIZES.md,
        className
      )}
      {...props}
    />
  );
});
