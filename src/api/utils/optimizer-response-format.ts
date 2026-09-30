/**
 * Converts optimizer API list fields (actions, summary, etc.) into UI-safe strings.
 * The backend may return strings or structured action objects.
 */

import type {
  OptimizerAction,
  OptimizerExplain,
  OptimizerExplainRow,
} from "../types/sql-optimizer.types";

/** Trims a value only when it is a non-empty string. */
function asTrimmedString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== ""
    ? value.trim()
    : undefined;
}

/** Coerces a value to a string array, dropping anything non-string. */
function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/** Coerces a value to a number array (action ids), dropping non-numbers. */
function asNumberArray(value: unknown): number[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is number => typeof item === "number");
}

function formatActionObject(record: Record<string, unknown>): string {
  const type =
    typeof record.type === "string" && record.type.trim() !== ""
      ? record.type.trim()
      : undefined;
  const reason =
    typeof record.reason === "string" && record.reason.trim() !== ""
      ? record.reason.trim()
      : undefined;

  if (type && reason) {
    return `${type}: ${reason}`;
  }
  if (reason) {
    return reason;
  }

  const ddl =
    typeof record.ddl === "string" && record.ddl.trim() !== ""
      ? record.ddl.trim()
      : undefined;
  if (ddl) {
    return ddl;
  }

  const hint =
    typeof record.hint === "string" && record.hint.trim() !== ""
      ? record.hint.trim()
      : undefined;
  if (hint) {
    return hint;
  }

  const parts: string[] = [];
  if (type) {
    parts.push(type);
  }
  if (typeof record.table === "string" && record.table.trim() !== "") {
    parts.push(`table ${record.table.trim()}`);
  }
  if (typeof record.before === "string" && record.before.trim() !== "") {
    parts.push(`before: ${record.before.trim()}`);
  }
  if (typeof record.after === "string" && record.after.trim() !== "") {
    parts.push(`after: ${record.after.trim()}`);
  }
  if (parts.length > 0) {
    return parts.join(" · ");
  }

  try {
    return JSON.stringify(record);
  } catch {
    return "Optimization action";
  }
}

export function formatOptimizerDisplayItem(item: unknown): string {
  if (typeof item === "string") {
    return item.trim();
  }
  if (item === null || item === undefined) {
    return "";
  }
  if (typeof item === "number" || typeof item === "boolean") {
    return String(item);
  }
  if (typeof item === "object") {
    const record = item as Record<string, unknown>;
    if (typeof record.message === "string" && record.message.trim() !== "") {
      return record.message.trim();
    }
    if (typeof record.text === "string" && record.text.trim() !== "") {
      return record.text.trim();
    }
    return formatActionObject(record);
  }
  return String(item);
}

export function coerceOptimizerDisplayList(items: unknown): string[] {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .map(formatOptimizerDisplayItem)
    .filter((line) => line.length > 0);
}

/**
 * Extracts the structured `actions` objects the live pipeline returns.
 *
 * `coerceOptimizerDisplayList` flattens these to "TYPE: reason" strings, which
 * is fine for the compact list but throws away the parts the "Analysis &
 * Explanation" section needs (priority, before/after, DDL, affected table).
 * Entries that are plain strings are skipped here — they carry no structure and
 * are already represented in the flattened list.
 */
export function coerceOptimizerActionList(items: unknown): OptimizerAction[] {
  if (!Array.isArray(items)) {
    return [];
  }

  const actions: OptimizerAction[] = [];
  for (const item of items) {
    if (typeof item !== "object" || item === null) continue;
    const record = item as Record<string, unknown>;

    const action: OptimizerAction = {
      id: typeof record.id === "number" ? record.id : undefined,
      type: asTrimmedString(record.type),
      priority: typeof record.priority === "number" ? record.priority : undefined,
      reason: asTrimmedString(record.reason),
      before: asTrimmedString(record.before),
      after: asTrimmedString(record.after),
      ddl: asTrimmedString(record.ddl),
      table: asTrimmedString(record.table),
      columns: Array.isArray(record.columns) ? asStringArray(record.columns) : undefined,
      hint: asTrimmedString(record.hint),
      issue_id: Array.isArray(record.issue_id) ? asNumberArray(record.issue_id) : undefined,
      conflicts_with: Array.isArray(record.conflicts_with)
        ? asNumberArray(record.conflicts_with)
        : undefined,
    };

    // An object with none of the known fields carries no display value.
    const hasContent = Object.values(action).some((value) => {
      if (value === undefined) return false;
      if (Array.isArray(value)) return value.length > 0;
      return value !== "";
    });
    if (hasContent) {
      actions.push(action);
    }
  }
  return actions;
}

/**
 * Extracts the raw EXPLAIN plan for the original query.
 *
 * The API nests the plan under `rows[]` with an `EXPLAIN` key; the value is a
 * pre-indented multi-line plan tree, so it is preserved verbatim.
 */
export function coerceOptimizerExplain(value: unknown): OptimizerExplain | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const rawRows = Array.isArray(record.rows) ? record.rows : [];

  const rows: OptimizerExplainRow[] = [];
  for (const row of rawRows) {
    if (typeof row !== "object" || row === null) continue;
    const plan = asTrimmedString((row as Record<string, unknown>).EXPLAIN);
    if (plan) {
      rows.push({ EXPLAIN: plan });
    }
  }

  if (rows.length === 0) {
    return undefined;
  }
  return { rows, columns: asStringArray(record.columns) };
}
