/**
 * Developer test-log helper.
 *
 * Emits a single, ready-to-paste tab-separated row (one per optimizer call)
 * that mirrors the "Dev Log" sheet built by tools/dev-test-log/Code.gs.
 * The column order below MUST stay byte-for-byte in sync with the
 * DEV_LOG_HEADERS constant in Code.gs so a copied row pastes into the sheet
 * with every value landing in the right column.
 *
 * Safety:
 * - The database password is never read into a row (dbLabel ignores it) and
 *   any secret-looking text inside the query / response is redacted.
 * - Runs only in dev builds unless re-enabled from the console (SQLDEV.enable()).
 */

import storage from "../../../utils/storage";

/** Sheet name the row is meant for — also printed to the console. */
export const DEV_LOG_SHEET_NAME = "Dev Log";

/** Column order — keep in sync with DEV_LOG_HEADERS in tools/dev-test-log/Code.gs. */
export const DEV_LOG_COLUMNS = [
  "When",
  "Tester",
  "API",
  "Query Sent",
  "DB",
  "Got Back",
  "Optimized?",
  "Same as my SQL?",
  "Connection OK?",
  "Quota left",
  "Took ms",
  "Problem",
  "Note",
] as const;

export type DevApi = "db_optimize_test" | "usage" | "db_optimize_full";
export type YesNoNa = "yes" | "no" | "n/a";
export type ProblemLevel = "none" | "minor" | "annoying" | "blocking";

export interface DevLogRow {
  api: DevApi;
  tester?: string;
  query?: string;
  db?: string;
  gotBack?: string;
  optimized?: YesNoNa;
  sameAsSql?: YesNoNa;
  connectionOk?: YesNoNa;
  quotaLeft?: number | null;
  tookMs?: number | null;
  problem?: ProblemLevel;
  note?: string;
}

const TESTER_STORAGE_KEY = "sql-optimizer-dev-tester";
const ENABLE_STORAGE_KEY = "sql-optimizer-dev-log-enabled";

export function getDevTester(): string {
  return storage.getItem<string>(TESTER_STORAGE_KEY) ?? "";
}

export function setDevTester(name: string): void {
  const clean = name.trim();
  storage.setItem(TESTER_STORAGE_KEY, clean);
  console.log(`[SQLDEV] Tester set to "${clean}" - auto rows will use it.`);
}

function enabledByDefault(): boolean {
  return Boolean(import.meta.env.DEV);
}

export function isDevLogEnabled(): boolean {
  const stored = storage.getItem<boolean>(ENABLE_STORAGE_KEY);
  return stored === null ? enabledByDefault() : stored;
}

export function setDevLogEnabled(enabled: boolean): void {
  storage.setItem(ENABLE_STORAGE_KEY, enabled);
  console.log(`[SQLDEV] Auto logging ${enabled ? "enabled" : "disabled"}.`);
}

/** Strip the DB password + common secret patterns from any free-text value. */
function redact(value: string): string {
  return value
    .replace(
      /((?:password|passwd|pwd|secret|token|api[_-]?key)\s*["']?\s*[:=]\s*["']?)[^\s"',;]+/gi,
      "$1***",
    )
    .replace(/(identified\s+by\s+)["']?\S+/gi, "$1***");
}

/** Flatten any value into a single TSV-safe cell (no tabs, no newlines). */
function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  return redact(String(value)).replace(/\s+/g, " ").trim();
}

/** Safe "host:port/database" label - never contains the password. */
export function dbLabel(cfg: {
  host: string;
  port: string;
  database: string;
}): string {
  const host = cfg.host.trim();
  const port = cfg.port.trim();
  const database = cfg.database.trim();
  if (!host && !database) return "syntax-only";
  const withPort = host ? `${host}${port ? `:${port}` : ""}` : "";
  return `${withPort}${database ? `${withPort ? "/" : ""}${database}` : ""}`;
}

type DevLogValues = Record<(typeof DEV_LOG_COLUMNS)[number], string>;

export function buildDevLogRow(row: DevLogRow): {
  header: string;
  line: string;
  values: DevLogValues;
} {
  const values: Record<(typeof DEV_LOG_COLUMNS)[number], string> = {
    When: new Date().toLocaleString(),
    Tester: cell(row.tester ?? getDevTester()) || "(set name)",
    API: cell(row.api),
    "Query Sent": cell(row.query),
    DB: cell(row.db),
    "Got Back": cell(row.gotBack),
    "Optimized?": cell(row.optimized ?? "n/a"),
    "Same as my SQL?": cell(row.sameAsSql ?? "n/a"),
    "Connection OK?": cell(row.connectionOk ?? "n/a"),
    "Quota left":
      row.quotaLeft === null || row.quotaLeft === undefined
        ? ""
        : String(row.quotaLeft),
    "Took ms":
      row.tookMs === null || row.tookMs === undefined
        ? ""
        : String(Math.round(row.tookMs)),
    Problem: cell(row.problem ?? "none"),
    Note: cell(row.note),
  };
  const header = DEV_LOG_COLUMNS.join("\t");
  const line = DEV_LOG_COLUMNS.map((column) => values[column]).join("\t");
  return { header, line, values };
}

/** Most recent TSV row printed, so SQLDEV.copy() can re-copy it. */
let lastTsvRow = "";

/** Keeps the one-off command reference from repeating across HMR reloads. */
let startupHintShown = false;

function copyToClipboard(text: string): void {
  try {
    void navigator.clipboard?.writeText(text);
  } catch {
    /* clipboard blocked - the row is still printed to the console */
  }
}

/**
 * Console styles. Every style sets BOTH colour and background so the text
 * stays readable on light and on dark DevTools themes (plain `color:#111827`
 * was invisible on the dark theme).
 */
const STYLE = {
  title:
    "background:#1d4ed8;color:#ffffff;padding:3px 10px;border-radius:4px;font-weight:700",
  ok: "background:#047857;color:#ffffff;padding:3px 10px;border-radius:4px;font-weight:700",
  bad: "background:#b91c1c;color:#ffffff;padding:3px 10px;border-radius:4px;font-weight:700",
  warn: "background:#b45309;color:#ffffff;padding:3px 10px;border-radius:4px;font-weight:700",
  hint: "background:#374151;color:#f9fafb;padding:2px 8px;border-radius:3px",
  code: "background:#111827;color:#f9fafb;padding:3px 8px;border-radius:3px;font-family:monospace",
} as const;

/** Print one paste-ready row (and copy it) for a single optimizer call. */
export function emitDevLogRow(row: DevLogRow): void {
  if (!isDevLogEnabled()) return;

  const { header, line, values } = buildDevLogRow(row);
  lastTsvRow = line;
  copyToClipboard(line);

  const failed = Boolean(row.problem) && row.problem !== "none";
  const verdict = failed ? `PROBLEM: ${row.problem}` : "OK";

  console.groupCollapsed(
    `%cSQLDEV %c${row.api} %c${verdict}%c  -> paste into "${DEV_LOG_SHEET_NAME}" (row copied)`,
    STYLE.title,
    STYLE.hint,
    failed ? STYLE.bad : STYLE.ok,
    STYLE.hint,
  );
  console.log("%cColumn values:", STYLE.hint);
  console.table(values);
  console.log("%cTSV header (sheet row 1, only if the sheet is new):", STYLE.hint);
  console.log(`%c${header}`, STYLE.code);
  console.log("%cTSV row (paste into the next empty row):", STYLE.hint);
  console.log(`%c${line}`, STYLE.code);
  if (!getDevTester()) {
    console.log(
      "%cTester is empty - run SQLDEV.setName(\"your name\") once and every later row uses it.",
      STYLE.warn,
    );
  }
  console.groupEnd();
}

/** Console usage reference - also shown once on first page load. */
export function printDevLogHelp(): void {
  console.groupCollapsed(
    "%cSQLDEV %cdeveloper test log - commands",
    STYLE.title,
    STYLE.hint,
  );
  console.log("%cSQLDEV.setName(\"Jainil\")  set your name (stored, one time)", STYLE.code);
  console.log("%cSQLDEV.name()             show the stored tester name", STYLE.code);
  console.log(
    "%cSQLDEV.test(\"connected\", 5)    log a manual Test Connection row",
    STYLE.code,
  );
  console.log(
    "%cSQLDEV.full({query, gotBack, optimized:\"yes\", tookMs:1200})  log a manual optimize row",
    STYLE.code,
  );
  console.log("%cSQLDEV.usage({quotaLeft:4})   log a manual /usage read", STYLE.code);
  console.log("%cSQLDEV.disable() / enable()   stop / start auto logging", STYLE.code);
  console.log("%cSQLDEV.copy()                 re-copy the last printed row", STYLE.code);
  console.log("%cSheet: " + DEV_LOG_SHEET_NAME, STYLE.hint);
  console.groupEnd();
}

/** Global console handle so a developer can log manual rows too. */
interface SqlDevConsole {
  setName: (name: string) => void;
  name: () => string;
  enable: () => void;
  disable: () => void;
  test: (gotBack?: string, quotaLeft?: number | null) => void;
  full: (opts: Partial<DevLogRow>) => void;
  usage: (opts?: Partial<DevLogRow>) => void;
  row: (opts: DevLogRow) => void;
  columns: () => string[];
  copy: () => void;
  help: () => void;
}

export function installDevLogConsole(): void {
  if (typeof window === "undefined") return;
  const handle: SqlDevConsole = {
    setName: setDevTester,
    name: getDevTester,
    enable: () => setDevLogEnabled(true),
    disable: () => setDevLogEnabled(false),
    test: (gotBack, quotaLeft) =>
      emitDevLogRow({
        api: "db_optimize_test",
        gotBack,
        quotaLeft,
        connectionOk: "yes",
        optimized: "n/a",
        sameAsSql: "n/a",
      }),
    full: (opts) =>
      emitDevLogRow({
        optimized: "n/a",
        sameAsSql: "n/a",
        connectionOk: "n/a",
        ...opts,
        api: "db_optimize_full",
      }),
    usage: (opts) =>
      emitDevLogRow({
        optimized: "n/a",
        sameAsSql: "n/a",
        ...opts,
        api: "usage",
      }),
    row: (opts) => emitDevLogRow(opts),
    columns: () => DEV_LOG_COLUMNS.slice(),
    copy: () => {
      if (!lastTsvRow) {
        console.log("%cSQLDEV: no row logged yet on this page.", STYLE.warn);
        return;
      }
      copyToClipboard(lastTsvRow);
      console.log("%cSQLDEV: last row copied again to the clipboard.", STYLE.ok);
    },
    help: printDevLogHelp,
  };
  (window as unknown as { SQLDEV: SqlDevConsole }).SQLDEV = handle;

  if (!startupHintShown && isDevLogEnabled()) {
    startupHintShown = true;
    printDevLogHelp();
  }
}
