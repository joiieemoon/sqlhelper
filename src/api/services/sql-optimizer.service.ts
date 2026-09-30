/**
 * SQL optimizer service.
 * POST {base}/db_optimize_test — validate DB connection
 * POST {base}/db_optimize_full — full live optimize pipeline
 */

import { AxiosError } from "axios";
import { axiosInstance } from "../client/axios";
import { SQL_OPTIMIZER_ENDPOINTS } from "../endpoints";
import {
  DatabaseConfig,
  DbTestConnectionRequest,
  DbTestConnectionResponse,
  OptimizeQueryRequest,
  OptimizeQueryResponse,
  OptimizerApiFailure,
  OptimizerRequestError,
  SqlOptimizerUsageResponse,
} from "../types";
import { API_CONFIG } from "../../config/api.config";
import storage from "../../utils/storage";
import {
  coerceOptimizerActionList,
  coerceOptimizerDisplayList,
  coerceOptimizerExplain,
} from "../utils/optimizer-response-format";

const DEVICE_ID_STORAGE_KEY = "sql-optimizer-device-id";

const OPTIMIZE_FAILED_MESSAGE =
  "Unable to optimize the query. Please try again.";

const TEST_FAILED_MESSAGE =
  "Unable to test the database connection. Please try again.";

/** Maps UI scope ids to backend hint strings. */
const SCOPE_TO_API: Record<string, string> = {
  joins: "joins",
  indexes: "indexes",
  keys: "keys",
  "sql-optimization": "sql_optimization",
};

interface ApiDatabaseConfigPayload {
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
  ssl_verify: boolean;
}

function getDeviceId(): string {
  const existing = storage.getItem<string>(DEVICE_ID_STORAGE_KEY);
  if (existing && existing.trim() !== "") {
    return existing;
  }

  const id =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `web-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;

  storage.setItem(DEVICE_ID_STORAGE_KEY, id);
  return id;
}

function toApiDatabaseConfig(
  config: DatabaseConfig,
): ApiDatabaseConfigPayload {
  const parsedPort = Number.parseInt(config.port, 10);
  return {
    host: config.host.trim(),
    port: Number.isFinite(parsedPort) ? parsedPort : 3306,
    user: config.user.trim(),
    password: config.password,
    database: config.database.trim(),
    ssl_verify: config.ssl_verify,
  };
}

function mapScopeToApi(scope: string[]): string[] {
  return scope.map((id) => SCOPE_TO_API[id] ?? id);
}

function pickToastMessage(data: Record<string, unknown>): string | undefined {
  const toast = data.toast;
  if (toast && typeof toast === "object" && toast !== null) {
    const message = (toast as { message?: unknown }).message;
    if (typeof message === "string" && message.trim() !== "") {
      return message;
    }
  }
  return undefined;
}

function parseFailureFromData(
  data: unknown,
  status?: number,
  fallback = OPTIMIZE_FAILED_MESSAGE,
): OptimizerApiFailure {
  const record =
    typeof data === "object" && data !== null
      ? (data as Record<string, unknown>)
      : {};

  const errorField = record.error;
  const messageFromError =
    typeof errorField === "string" && errorField.trim() !== ""
      ? errorField
      : undefined;

  const message =
    pickToastMessage(record) ??
    messageFromError ??
    (typeof record.message === "string" ? record.message : undefined) ??
    fallback;

  const toast =
    record.toast && typeof record.toast === "object"
      ? (record.toast as OptimizerApiFailure["toast"])
      : undefined;

  const error_code =
    typeof record.error_code === "string" ? record.error_code : undefined;

  const limit =
    typeof record.limit === "number" ? record.limit : undefined;
  const queries_used =
    typeof record.queries_used === "number" ? record.queries_used : undefined;
  const queries_remaining =
    typeof record.queries_remaining === "number"
      ? record.queries_remaining
      : undefined;
  const reset_time =
    typeof record.reset_time === "string" ? record.reset_time : undefined;
  const resets_in =
    typeof record.resets_in === "number" ? record.resets_in : undefined;

  return {
    message,
    error_code,
    toast,
    limit,
    queries_used,
    queries_remaining,
    reset_time,
    resets_in,
    status,
  };
}

function normalizeOptimizeResponse(
  data: Record<string, unknown>,
): OptimizeQueryResponse {
  const changes = coerceOptimizerDisplayList(data.changes);
  const summaryFromApi = coerceOptimizerDisplayList(data.summary);
  const summary =
    summaryFromApi.length > 0 ? summaryFromApi : changes;

  const limit = typeof data.limit === "number" ? data.limit : undefined;
  const queriesUsed =
    typeof data.queries_used === "number" ? data.queries_used : null;
  const queriesRemaining =
    typeof data.queries_remaining === "number"
      ? data.queries_remaining
      : null;
  const resetsIn =
    typeof data.resets_in === "number" ? data.resets_in : undefined;

  // Structured form of `actions` — kept alongside the flattened strings because
  // the "Analysis & Explanation" section needs type / priority / before / after
  // / DDL, which the flattened "TYPE: reason" form discards.
  const actionDetails = coerceOptimizerActionList(data.actions);
  const explain = coerceOptimizerExplain(data.explain);
  const appliedActions = Array.isArray(data.applied_actions)
    ? data.applied_actions.filter((id): id is number => typeof id === "number")
    : undefined;
  const rewriteActions = Array.isArray(data.rewrite_actions)
    ? data.rewrite_actions.filter((id): id is number => typeof id === "number")
    : undefined;
  const noOpActions = Array.isArray(data.no_op_actions)
    ? data.no_op_actions.filter((id): id is number => typeof id === "number")
    : undefined;
  const skippedActions = Array.isArray(data.skipped_actions)
    ? data.skipped_actions.filter((id): id is number => typeof id === "number")
    : undefined;

  return {
    success: data.success === true,
    optimized_query:
      typeof data.optimized_query === "string" ? data.optimized_query : "",
    actions: coerceOptimizerDisplayList(data.actions),
    actionDetails: actionDetails.length > 0 ? actionDetails : undefined,
    index_recommendations: coerceOptimizerDisplayList(
      data.index_recommendations,
    ),
    summary,
    changes: changes.length > 0 ? changes : undefined,
    message: typeof data.message === "string" ? data.message : undefined,
    grounded: typeof data.grounded === "boolean" ? data.grounded : undefined,
    schema_warnings: Array.isArray(data.schema_warnings)
      ? (data.schema_warnings as string[])
      : undefined,
    error:
      typeof data.error === "string"
        ? data.error
        : data.error === null
          ? null
          : null,
    error_code:
      typeof data.error_code === "string" ? data.error_code : undefined,
    toast:
      data.toast && typeof data.toast === "object"
        ? (data.toast as OptimizeQueryResponse["toast"])
        : undefined,
    limit,
    queries_used: queriesUsed,
    queries_remaining: queriesRemaining,
    reset_time:
      typeof data.reset_time === "string" ? data.reset_time : undefined,
    resets_in: resetsIn,
    elapsed_ms:
      typeof data.elapsed_ms === "number" ? data.elapsed_ms : undefined,
    engine: typeof data.engine === "string" ? data.engine : undefined,
    database: typeof data.database === "string" ? data.database : undefined,
    query_changed:
      typeof data.query_changed === "boolean" ? data.query_changed : undefined,
    explain,
    applied_actions: appliedActions,
    rewrite_actions: rewriteActions,
    no_op_actions: noOpActions,
    skipped_actions: skippedActions,
    rewriter_warnings: Array.isArray(data.rewriter_warnings)
      ? coerceOptimizerDisplayList(data.rewriter_warnings)
      : undefined,
  };
}

function optimizerRequestConfig(timeoutMs: number) {
  return {
    baseURL: API_CONFIG.sqlOptimizerBaseUrl,
    timeout: timeoutMs,
    headers: {
      "X-Device-ID": getDeviceId(),
    },
  };
}

function throwFromAxios(error: unknown, fallback: string): never {
  if (error instanceof OptimizerRequestError) {
    throw error;
  }

  const axiosError = error as AxiosError;
  const status = axiosError.response?.status;
  const failure = parseFailureFromData(
    axiosError.response?.data,
    status,
    fallback,
  );
  throw new OptimizerRequestError(failure);
}

/**
 * Test database connectivity (fast path — does not consume optimize quota).
 */
export async function testDatabaseConnection(
  database_config: DatabaseConfig,
): Promise<DbTestConnectionResponse> {
  const body: DbTestConnectionRequest = {
    database_config,
    query: "SELECT 1",
  };

  try {
    const response = await axiosInstance.post<DbTestConnectionResponse>(
      SQL_OPTIMIZER_ENDPOINTS.DB_OPTIMIZE_TEST.path,
      {
        database_config: toApiDatabaseConfig(database_config),
        query: body.query,
      },
      optimizerRequestConfig(API_CONFIG.sqlOptimizerTestTimeoutMs),
    );

    const data = response.data;
    if (data.success) {
      return {
        ...data,
        limit: typeof data.limit === "number" ? data.limit : undefined,
        queries_used:
          typeof data.queries_used === "number" ? data.queries_used : undefined,
        queries_remaining:
          typeof data.queries_remaining === "number"
            ? data.queries_remaining
            : undefined,
        resets_in:
          typeof data.resets_in === "number" ? data.resets_in : undefined,
      };
    }

    const failure = parseFailureFromData(
      data,
      response.status,
      data.error ?? TEST_FAILED_MESSAGE,
    );
    throw new OptimizerRequestError(failure);
  } catch (error) {
    throwFromAxios(error, TEST_FAILED_MESSAGE);
  }
}

/**
 * Full query optimization against a live database.
 */
export async function optimizeSqlQuery(
  request: OptimizeQueryRequest,
): Promise<OptimizeQueryResponse> {
  try {
    const response = await axiosInstance.post<Record<string, unknown>>(
      SQL_OPTIMIZER_ENDPOINTS.DB_OPTIMIZE_FULL.path,
      {
        database_config: toApiDatabaseConfig(request.database_config),
        query: request.query,
        scope: mapScopeToApi(request.scope),
        custom_request: request.custom_request,
      },
      optimizerRequestConfig(API_CONFIG.sqlOptimizerTimeoutMs),
    );

    const normalized = normalizeOptimizeResponse(response.data);

    if (normalized.error && normalized.error.trim() !== "") {
      const failure = parseFailureFromData(response.data, response.status);
      throw new OptimizerRequestError(failure);
    }

    if (response.data.success === false) {
      const failure = parseFailureFromData(response.data, response.status);
      throw new OptimizerRequestError(failure);
    }

    return normalized;
  } catch (error) {
    throwFromAxios(error, OPTIMIZE_FAILED_MESSAGE);
  }
}

/**
 * Retrieve authoritative quota usage for the current device (does not consume quota).
 */
export async function getOptimizerUsage(): Promise<SqlOptimizerUsageResponse> {
  try {
    const response = await axiosInstance.get<Record<string, unknown>>(
      SQL_OPTIMIZER_ENDPOINTS.USAGE.path,
      optimizerRequestConfig(10_000),
    );

    const data = response.data;
    const limit = typeof data.limit === "number" ? data.limit : 5;
    const queries_used =
      typeof data.queries_used === "number" ? data.queries_used : 0;
    const queries_remaining =
      typeof data.queries_remaining === "number"
        ? data.queries_remaining
        : Math.max(0, limit - queries_used);
    const resets_in =
      typeof data.resets_in === "number" ? data.resets_in : undefined;
    const identifier =
      typeof data.identifier === "string" ? data.identifier : undefined;
    const allowed =
      typeof data.allowed === "boolean" ? data.allowed : queries_remaining > 0;
    const error = typeof data.error === "string" ? data.error : null;

    return {
      identifier,
      limit,
      queries_used,
      queries_remaining,
      resets_in,
      allowed,
      error,
    };
  } catch (error) {
    throwFromAxios(error, "Unable to fetch usage quota.");
  }
}

