/**
 * SOP Review Table — Traffic-Light Review as a real data grid.
 *
 * The backend (`sop_table_generator.generate_traffic_light_table`) returns the
 * review as a markdown pipe-table string. This module:
 *   1. parses that markdown into typed rows (`parseSopMarkdownTable`),
 *   2. renders it with the shared `Table` primitives plus status `Badge`s
 *      (`SopReviewTable`), matching the rest of the dashboard.
 *
 * Falls back to the raw text when the payload is not markdown-shaped, so a
 * backend format change can never blank the section.
 */

export type SopStatus = "green" | "orange" | "red" | "unknown";

export interface SopTableRow {
  check: string;
  status: SopStatus;
  /** Status text as emitted by the backend (e.g. "Green - OK"). */
  statusLabel: string;
  /**
   * Human-readable detail. NOTE: the current backend repeats the status
   * label in the Description column, so this usually mirrors `statusLabel`.
   * When the backend is enriched with real per-check text, the frontend
   * picks it up with no further change.
   */
  description: string;
}

const STATUS_PATTERNS: Array<{ status: SopStatus; needles: string[] }> = [
  { status: "green", needles: ["\u2705", "green"] },
  { status: "orange", needles: ["\u26a0", "orange", "investigate"] },
  { status: "red", needles: ["\u274c", "\u274e", "red", "fix required"] },
];

/** Map the check/description markers in a markdown row to a traffic-light status. */
function detectStatus(cells: string[]): { status: SopStatus; statusLabel: string } {
  const joined = cells.join(" ").toLowerCase();
  for (const { status, needles } of STATUS_PATTERNS) {
    if (needles.some((needle) => joined.includes(needle))) {
      return { status, statusLabel: cells[cells.length - 1] ?? "" };
    }
  }
  return { status: "unknown", statusLabel: cells[cells.length - 1] ?? "" };
}

function isSeparatorRow(cells: string[]): boolean {
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell));
}

/**
 * Parse a markdown pipe-table into header + typed rows.
 * Returns `null` when the text is not a parseable table.
 */
export function parseSopMarkdownTable(markdown: string): {
  headers: string[];
  rows: SopTableRow[];
} | null {
  const lines = markdown
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("|") && line.endsWith("|"));
  if (lines.length < 2) return null;

  const splitRow = (line: string): string[] =>
    line
      .slice(1, -1)
      .split("|")
      .map((cell) => cell.trim());

  const headerCells = splitRow(lines[0]);
  const bodyLines = isSeparatorRow(splitRow(lines[1])) ? lines.slice(2) : lines.slice(1);
  if (bodyLines.length === 0) return null;

  const rows: SopTableRow[] = bodyLines.map((line) => {
    const cells = splitRow(line);
    const { status, statusLabel } = detectStatus(cells);
    // Backend header: Check | Green | Orange | Red | Description.
    // First cell = metric name, last cell = human-readable detail. The
    // middle marker cells (✅/⚠️/❌) only carry the status, so skip them.
    const detail = cells.length > 1 ? (cells[cells.length - 1] ?? "") : "";
    return {
      check: cells[0] ?? "",
      status,
      statusLabel: detail || statusLabel,
      description: detail,
    };
  });

  return { headers: headerCells, rows };
}

/** Summarise row counts per status for the table caption. */
export function summarizeSopRows(rows: SopTableRow[]): {
  green: number;
  orange: number;
  red: number;
  total: number;
} {
  return {
    green: rows.filter((row) => row.status === "green").length,
    orange: rows.filter((row) => row.status === "orange").length,
    red: rows.filter((row) => row.status === "red").length,
    total: rows.length,
  };
}
