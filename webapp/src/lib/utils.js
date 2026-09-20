import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * 合并 Tailwind 类名。
 *
 * clsx 负责把条件/数组/对象形式的类名拼平，twMerge 负责消解"同一属性被写了两遍"
 * 的冲突（例如外部传入的 ``p-2`` 应该覆盖组件默认的 ``p-4``）。
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
