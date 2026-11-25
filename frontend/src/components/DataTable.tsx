import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/motion";

type Props<T> = {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  getRowId: (row: T) => string;
  /** none: plain table; single: click a row to select it; multi: checkbox column */
  selection?: "none" | "single" | "multi";
  selected?: string[];
  onSelectedChange?: (ids: string[]) => void;
  highlightId?: string | null;
  emptyText?: string;
  maxHeight?: string;
  isLoading?: boolean;
  className?: string;
  /** Click-to-sort headers. Turn off for server-paginated data: sorting one page would mislead. */
  sortable?: boolean;
  /** What a row's checkbox is announced as (default "Select row") */
  rowLabel?: (row: T) => string;
};

export function DataTable<T>({
  columns,
  data,
  getRowId,
  selection = "none",
  selected = [],
  onSelectedChange,
  highlightId,
  emptyText = "No rows",
  maxHeight = "32rem",
  isLoading,
  className,
  sortable = true,
  rowLabel,
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const rowSelection = useMemo<RowSelectionState>(
    () => Object.fromEntries(selected.map((id) => [id, true])),
    [selected],
  );

  const allColumns = useMemo<ColumnDef<T, unknown>[]>(() => {
    if (selection !== "multi") return columns;
    return [
      {
        id: "__select",
        enableSorting: false,
        header: ({ table }) => (
          <Checkbox
            aria-label="Select all"
            checked={table.getIsAllRowsSelected() ? true : table.getIsSomeRowsSelected() ? "indeterminate" : false}
            onCheckedChange={(v) => table.toggleAllRowsSelected(!!v)}
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            aria-label={rowLabel ? rowLabel(row.original) : "Select row"}
            checked={row.getIsSelected()}
            onCheckedChange={(v) => row.toggleSelected(!!v)}
            onClick={(e) => e.stopPropagation()}
          />
        ),
        size: 32,
      },
      ...columns,
    ];
  }, [columns, selection, rowLabel]);

  // TanStack Table returns non-memoizable functions; the React Compiler skips this component (expected)
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data,
    columns: allColumns,
    getRowId,
    state: { sorting, rowSelection },
    onSortingChange: setSorting,
    enableSorting: sortable,
    enableRowSelection: selection !== "none",
    enableMultiRowSelection: selection === "multi",
    onRowSelectionChange: (updater) => {
      const next = typeof updater === "function" ? updater(rowSelection) : updater;
      onSelectedChange?.(Object.keys(next).filter((k) => next[k]));
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className={cn("overflow-auto rounded-md border", className)} style={{ maxHeight }}>
      <Table>
        <TableHeader className="bg-muted/60 sticky top-0 z-10 backdrop-blur">
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const canSort = header.column.getCanSort() && header.column.id !== "__select";
                const dir = header.column.getIsSorted();
                return (
                  <TableHead key={header.id} style={{ width: header.column.columnDef.size }}>
                    {header.isPlaceholder ? null : canSort ? (
                      <button
                        type="button"
                        className="hover:text-foreground inline-flex items-center gap-1"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {dir === "asc" ? (
                          <ArrowUp className="size-3" />
                        ) : dir === "desc" ? (
                          <ArrowDown className="size-3" />
                        ) : (
                          <ArrowUpDown className="size-3 opacity-40" />
                        )}
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length === 0 && isLoading ? (
            [0, 1, 2, 3, 4].map((i) => (
              <TableRow key={`loading-${i}`} aria-hidden>
                {allColumns.map((_, c) => (
                  <TableCell key={c}>
                    <Skeleton className="h-4 w-full max-w-48" />
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : table.getRowModel().rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={allColumns.length} className="text-muted-foreground h-20 text-center">
                {emptyText}
              </TableCell>
            </TableRow>
          ) : (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                data-row-id={row.id}
                data-state={row.getIsSelected() ? "selected" : undefined}
                aria-selected={selection === "none" ? undefined : row.getIsSelected()}
                // Single-select rows are the only way to pick an item, so they must work from the keyboard
                tabIndex={selection === "single" ? 0 : undefined}
                className={cn(
                  selection !== "none" && "cursor-pointer",
                  selection === "single" && "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                  highlightId === row.id && "bg-primary/10 hover:bg-primary/15",
                )}
                onClick={selection === "none" ? undefined : () => row.toggleSelected(!row.getIsSelected())}
                onKeyDown={
                  selection === "single"
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          row.toggleSelected(!row.getIsSelected());
                        }
                      }
                    : undefined
                }
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id} className="whitespace-normal">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
