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
 * Response returned by POST /db_optimize_full.
 */
export interface OptimizeQueryResponse {
  success?: boolean;
  optimized_query: string;
  actions: string[];
  index_recommendations: string[];
  summary: string[];
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
