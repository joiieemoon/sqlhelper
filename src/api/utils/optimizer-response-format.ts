/**
 * Converts optimizer API list fields (actions, summary, etc.) into UI-safe strings.
 * The backend may return strings or structured action objects.
 */

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
