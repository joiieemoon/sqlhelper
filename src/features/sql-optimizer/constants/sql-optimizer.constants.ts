/**
 * SQL optimizer constants.
 * Static defaults for the query optimizer page.
 */

import type {
  DatabaseConfig,
  OptimizeScope,
} from "../types/sql-optimizer.types";

/** Empty editor on first visit — users paste or restore a saved query from storage. */
export const DEFAULT_SQL_QUERY = "";

/**
 * Optimization scopes shown as chips and sent as the request `scope` array.
 * The optimizer endpoint accepts these as free-form hint strings.
 */
export const OPTIMIZE_SCOPES: OptimizeScope[] = [
  { id: "joins", label: "Joins" },
  { id: "indexes", label: "Indexes" },
  { id: "keys", label: "Keys" },
  { id: "sql-optimization", label: "SQL Optimization" },
];

/** Scopes selected by default. */
export const DEFAULT_SELECTED_SCOPE_IDS: string[] = ["sql-optimization"];

/** Empty connection details — the optimizer then runs syntax-only. */
export const DEFAULT_DATABASE_CONFIG: DatabaseConfig = {
  host: "",
  port: "",
  user: "",
  password: "",
  database: "",
  ssl_verify: false,
};

/** Daily query allowance enforced by the optimizer API. */
export const QUERY_LIMIT = 5;

/** Shortcut hint shown next to the Optimize Query button. */
export const OPTIMIZE_SHORTCUT_LABEL = "Ctrl + Enter";

/** Info box shown below the database configuration fields. */
export const DATABASE_CONFIG_HINT =
  "Use Test Connection to verify credentials before optimizing. For Aiven/MySQL cloud hosts, leave SSL verify off unless your provider requires it. Host, user, database, and your SQL query are saved in this browser; password is not stored — enter it each time.";

/**
 * Shown next to the Optimize button (and used by the Ctrl+Enter guard) once the
 * daily quota is exhausted — the API would reject the request anyway.
 */
export const QUERY_LIMIT_REACHED_MESSAGE =
  "Daily query limit reached — no queries remaining today. The limit resets daily.";

/**
 * Shown in the config panel while the connection details are missing or
 * incomplete (including an untouched, all-empty form), so the disabled Test
 * Connection / Optimize buttons always have a visible reason. A complete config
 * is required before either action can run.
 */
export const DATABASE_CONFIG_INCOMPLETE_HINT =
  "Enter the database connection details — host, port, user, password and database — to enable Test Connection and Optimize Query.";

export const OPTIMIZATION_PHASES = [
  "Verifying database connection…",
  "Extracting schema & EXPLAIN plan…",
  "Running AI optimizer (this may take several minutes)…",
] as const;
