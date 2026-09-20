import { useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ChevronDown, ChevronsUpDown, ChevronUp } from "lucide-react";
import { cn } from "../../lib/utils.js";
import { Table, TBody, Td, Th, THead, Tr } from "./table.jsx";

/**
 * 基于 TanStack Table 的通用表格。
 *
 * 只保留本项目实际需要的两个能力：**列排序**与**行点击/选中态**，其余
 * （分页、筛选、列宽拖拽）按"不做多余抽象"的原则一律不加。
 * 列定义直接复用 TanStack 的 ColumnDef，``enableSorting: false`` 可关掉某列排序。
 */
export function DataTable({
  columns,
  data,
  empty = "暂无数据。",
  getRowKey,
  onRowClick,
  isRowActive,
  className,
}) {
  const [sorting, setSorting] = useState([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const rows = table.getRowModel().rows;

  return (
    <Table className={className}>
      <THead>
        {table.getHeaderGroups().map((group) => (
          <Tr key={group.id} className="border-border">
            {group.headers.map((header) => {
              const sortable = header.column.getCanSort();
              const direction = header.column.getIsSorted();
              return (
                <Th key={header.id} className={sortable ? "p-0" : undefined}>
                  {sortable ? (
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      className="inline-flex w-full cursor-pointer items-center gap-1 px-3 py-2.5 text-left text-xs font-medium tracking-wide text-muted transition-colors hover:text-fg"
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      <SortIcon direction={direction} />
                    </button>
                  ) : (
                    flexRender(header.column.columnDef.header, header.getContext())
                  )}
                </Th>
              );
            })}
          </Tr>
        ))}
      </THead>
      <TBody>
        {rows.map((row) => {
          const active = isRowActive?.(row.original);
          return (
            <Tr
              key={getRowKey ? getRowKey(row.original) : row.id}
              className={cn(
                onRowClick && "clickable cursor-pointer hover:bg-hover",
                active && "row-selected"
              )}
              onClick={onRowClick ? () => onRowClick(row.original) : undefined}
            >
              {row.getVisibleCells().map((cell) => (
                <Td key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </Td>
              ))}
            </Tr>
          );
        })}
        {rows.length === 0 && (
          <Tr>
            <Td colSpan={columns.length} className="py-8 text-center text-muted">
              {empty}
            </Td>
          </Tr>
        )}
      </TBody>
    </Table>
  );
}

function SortIcon({ direction }) {
  if (direction === "asc") return <ChevronUp size={13} className="shrink-0" />;
  if (direction === "desc") return <ChevronDown size={13} className="shrink-0" />;
  return <ChevronsUpDown size={13} className="shrink-0 opacity-35" />;
}
