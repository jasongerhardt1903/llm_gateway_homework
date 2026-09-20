import { cn } from "../../lib/utils.js";

/** 表格原语。外层负责圆角/描边/横向滚动，内层是语义化的 table。 */
export function Table({ className, children }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-panel">
      <table className={cn("w-full border-collapse", className)}>{children}</table>
    </div>
  );
}

export function THead({ className, children }) {
  return <thead className={cn("bg-panel2", className)}>{children}</thead>;
}

export function TBody({ children }) {
  return <tbody>{children}</tbody>;
}

export function Tr({ className, children, ...props }) {
  return (
    <tr
      className={cn("border-b border-border-soft transition-colors last:border-b-0", className)}
      {...props}
    >
      {children}
    </tr>
  );
}

export function Th({ className, children, ...props }) {
  return (
    <th
      className={cn(
        "whitespace-nowrap px-3 py-2.5 text-left text-xs font-medium tracking-wide text-muted",
        className
      )}
      {...props}
    >
      {children}
    </th>
  );
}

export function Td({ className, children, ...props }) {
  return (
    <td className={cn("px-3 py-2.5 align-top text-sm text-fg2", className)} {...props}>
      {children}
    </td>
  );
}
