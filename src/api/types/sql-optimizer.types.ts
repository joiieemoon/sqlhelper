/**
 * SQL query optimizer API types.
 * Mirrors POST /db_optimize_test and POST /db_optimize_full.
 */

export interface ApiToast {
  type: "warning" | "error" | "success" | "info";
  title?: string;
  message: string;
}

/**
 * Request payload for a query optimization.
 */
export interface OptimizeQueryRequest {
  query: string;
  scope: string[];
  custom_request: string;
  database_config: DatabaseConfig;
}

/**
 * Database connection details sent with optimizer requests.
 */
export interface DatabaseConfig {
  host: string;
  port: string;
  user: string;
  password: string;
  database: string;
  ssl_verify: boolean;
}

export interface DbTestConnectionRequest {
  database_config: DatabaseConfig;
  query: string;
}

export interface DbTestConnectionResponse {
  success: boolean;
  message?: string;
  error?: string | null;
  error_code?: string;
  toast?: ApiToast;
  limit?: number;
  queries_used?: number;
  queries_remaining?: number;
  resets_in?: number;
}

/**
 * Authoritative quota and usage returned by GET /usage and response counters.
 */
export interface SqlOptimizerUsageResponse {
  identifier?: string;
  limit: number;
  queries_used: number;
  queries_remaining: number;
  resets_in?: number;
  allowed?: boolean;
  error?: string | null;
}

/**
 * One structured optimizer action as returned by the live pipeline.
 *
 * The API sends these as objects (not strings) — `reason` is where the engine
 * explains the rewrite in terms of the actual EXPLAIN plan, which is the
 * content behind the "Analysis & Explanation" section.
 */
export interface OptimizerAction {
  id?: number;
  /** Machine action code, e.g. `PUSH_DOWN_AGGREGATION`. */
  type?: string;
  /** 1 = highest. */
  priority?: number;
  /** Why the engine made this change, referencing plan evidence. */
  reason?: string;
  /** SQL before the change (empty for index-only suggestions). */
  before?: string;
  /** SQL after the change. */
  after?: string;
  /** DDL for a suggested index, when the action proposes one. */
  ddl?: string;
  table?: string;
  columns?: string[];
  hint?: string;
  issue_id?: number[];
  conflicts_with?: number[];
}

/** One row of the MySQL EXPLAIN output. */
export interface OptimizerExplainRow {
  /** The plan tree line(s), newline-indented exactly as MySQL returned them. */
  EXPLAIN?: string;
}

/** Raw EXPLAIN block for the ORIGINAL query (there is no after-plan yet). */
export interface OptimizerExplain {
  rows?: OptimizerExplainRow[];
  columns?: string[];
}

/**
 * Response returned by POST /db_optimize_full.
 */
export interface OptimizeQueryResponse {
  success?: boolean;
  optimized_query: string;
  actions: string[];
  index_recommendations: string[];
  summary: string[];
  /**
   * Structured actions, preserved alongside the flattened `actions` strings.
   * Drives the "Analysis & Explanation" section.
   */
  actionDetails?: OptimizerAction[];
  changes?: string[];
  message?: string;
  grounded?: boolean;
  schema_warnings?: string[];
  error: string | null;
  error_code?: string;
  toast?: ApiToast;
  limit?: number;
  queries_used: number | null;
  queries_remaining: number | null;
  reset_time?: string;
  resets_in?: number;
  elapsed_ms?: number;
  /** Diagnostics metadata shown in the "Report" section. */
  engine?: string;
  database?: string;
  query_changed?: boolean;
  explain?: OptimizerExplain;
  /** Action ids by disposition — applied / rewritten / no-op / skipped. */
  applied_actions?: number[];
  rewrite_actions?: number[];
  no_op_actions?: number[];
  skipped_actions?: number[];
  rewriter_warnings?: string[];
}

/** Parsed failure from the optimizer API (HTTP 4xx/5xx or success:false). */
export interface OptimizerApiFailure {
  message: string;
  error_code?: string;
  toast?: ApiToast;
  limit?: number;
  queries_used?: number;
  queries_remaining?: number;
  reset_time?: string;
  resets_in?: number;
  status?: number;
}

export class OptimizerRequestError extends Error {
  readonly failure: OptimizerApiFailure;

  constructor(failure: OptimizerApiFailure) {
    super(failure.message);
    this.name = "OptimizerRequestError";
    this.failure = failure;
  }
}
