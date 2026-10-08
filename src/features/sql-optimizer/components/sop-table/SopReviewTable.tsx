/**
 * SopReviewTable — renders the Traffic-Light Review as a real data grid.
 *
 * Columns: Check | Status (badge) | Description.
 * Row tint follows the traffic light: green / orange / red backgrounds so a
 * failing check is visible at a glance. Copy keeps working on the raw
 * markdown text via the existing parent button.
 */

import Badge from "../../../../components/ui/badge/Badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableRow,
} from "../../../../components/ui/table";
import type { SopStatus, SopTableRow } from "./sop-table-parse";

const STATUS_BADGE: Record<SopStatus, { color: "success" | "warning" | "error" | "light"; dot: string; label: string }> = {
  green: { color: "success", dot: "bg-success-500", label: "Green — OK" },
  orange: { color: "warning", dot: "bg-warning-500", label: "Orange — Investigate" },
  red: { color: "error", dot: "bg-error-500", label: "Red — Fix Required" },
  unknown: { color: "light", dot: "bg-gray-400", label: "Unknown" },
};

const ROW_TINT: Record<SopStatus, string> = {
  green: "bg-success-50/50 dark:bg-success-500/[0.04]",
  orange: "bg-warning-50/60 dark:bg-warning-500/[0.06]",
  red: "bg-error-50/60 dark:bg-error-500/[0.06]",
  unknown: "",
};

interface SopReviewTableProps {
  rows: SopTableRow[];
}

const SopReviewTable: React.FC<SopReviewTableProps> = ({ rows }) => {
  return (
    <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800">
      <Table className="text-sm">
        <TableHeader className="border-b border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-white/[0.02]">
          <TableRow>
            <TableCell isHeader className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Check
            </TableCell>
            <TableCell isHeader className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Status
            </TableCell>
            <TableCell isHeader className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Description
            </TableCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => {
            const badge = STATUS_BADGE[row.status];
            return (
              <TableRow
                key={`sop-row-${index}`}
                className={`border-b border-gray-100 last:border-0 dark:border-gray-800 ${ROW_TINT[row.status]}`}
              >
                <TableCell className="px-4 py-3 align-top font-medium text-gray-800 dark:text-white/90">
                  {row.check}
                </TableCell>
                <TableCell className="px-4 py-3 align-top">
                  <Badge color={badge.color} size="sm">
                    <span className={`mr-1.5 inline-block size-2 rounded-full ${badge.dot}`} />
                    {badge.label}
                  </Badge>
                </TableCell>
                <TableCell className="px-4 py-3 align-top text-gray-600 dark:text-gray-300">
                  {row.description}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
};

export default SopReviewTable;
