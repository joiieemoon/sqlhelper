
//  * SQL optimizer state hook.
//  * Owns the optimizer form state, the optimization request flow and the
//  * Ctrl + Enter keyboard shortcut.
//  */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useOptimizeSqlQuery,
  useSqlOptimizerUsage,
  useTestDbConnection,
} from "../../../api/hooks";
import type {
  OptimizeQueryRequest,
  OptimizeQueryResponse,
} from "../../../api/types";
import { OptimizerRequestError } from "../../../api/types";
import {
  toastError,
  toastSuccess,
  toastWarning,
} from "../../../components/common/toast";
import {
  OPTIMIZATION_PHASES,
  QUERY_LIMIT_REACHED_MESSAGE,
} from "../constants/sql-optimizer.constants";
import type {
  ConnectionStatus,
  DatabaseConfig,
  DatabaseConfigTouched,
  SqlQueryUsage,
} from "../types/sql-optimizer.types";
import {
  getFirstDatabaseConfigError,
  getVisibleDatabaseConfigErrors,
  isDatabaseConfigComplete,
  validateDatabaseConfigFields,
} from "../utils/database-config-validation";
import { validateQueryGuard, validateSqlQuery } from "../utils/sql-validation";
import {
  getInitialOptimizerForm,
  persistOptimizerForm,
} from "../utils/form-persistence";
import {
  dbLabel,
  emitDevLogRow,
  installDevLogConsole,
} from "../utils/dev-test-log";
import storage from "../../../utils/storage";
import { API_CONFIG } from "../../../config/api.config";

const OPTIMIZE_FAILED_MESSAGE =
  "Unable to optimize the query. Please try again.";

function formatTimeoutMinutes(ms: number): number {
  return Math.max(1, Math.round(ms / 60_000));
}

function getOptimizeErrorMessage(error: unknown): string {
  const err = error as { code?: string; message?: string };
  const isTimeout =
    err.code === "ECONNABORTED" || /timeout/i.test(err.message ?? "");
  if (isTimeout) {
    const minutes = formatTimeoutMinutes(API_CONFIG.sqlOptimizerTimeoutMs);
    return `AI optimization timed out after ${minutes} minutes. The backend may still be running — try again or use a simpler query.`;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return OPTIMIZE_FAILED_MESSAGE;
}

/** localStorage key holding today's usage so the count survives reloads. */
const USAGE_STORAGE_KEY = "sql-optimizer-usage";

/** Stored usage shape: counts plus the day they belong to (daily quota). */
interface StoredUsage extends SqlQueryUsage {
  /** Local date string (YYYY-MM-DD) the counts belong to. */
  date: string;
}

function todayDateString(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function loadStoredUsage(): SqlQueryUsage | null {
  const stored = storage.getItem<StoredUsage>(USAGE_STORAGE_KEY);
  if (!stored || stored.date !== todayDateString()) return null;
  if (
    typeof stored.queriesUsed !== "number" ||
    typeof stored.queriesRemaining !== "number"
  ) {
    return null;
  }
  return {
    queriesUsed: stored.queriesUsed,
    queriesRemaining: stored.queriesRemaining,
    resetTime: stored.resetTime,
    resetsIn: stored.resetsIn,
    limit: stored.limit,
  };
}

function persistUsage(usage: SqlQueryUsage): void {
  storage.setItem<StoredUsage>(USAGE_STORAGE_KEY, {
    ...usage,
    date: todayDateString(),
  });
}

function showApiToast(
  toast: { type?: string; title?: string; message: string } | undefined,
  fallbackMessage: string,
  fallbackType: "error" | "warning" | "success" = "error",
): void {
  const message = toast?.message?.trim() || fallbackMessage;
  const type = toast?.type ?? fallbackType;

  switch (type) {
    case "warning":
      toastWarning(message);
      break;
    case "success":
      toastSuccess(message);
      break;
    case "info":
      toastSuccess(message);
      break;
    default:
      toastError(message);
  }
}

export function useSqlOptimizer() {
  const [databaseConfig, setDatabaseConfig] = useState<DatabaseConfig>(
    () => getInitialOptimizerForm().databaseConfig,
  );
  const [query, setQuery] = useState(() => getInitialOptimizerForm().query);
  const [selectedScopeIds, setSelectedScopeIds] = useState<string[]>(
    () => getInitialOptimizerForm().selectedScopeIds,
  );
  const [customRequest, setCustomRequest] = useState(
    () => getInitialOptimizerForm().customRequest,
  );
  const [usage, setUsage] = useState<SqlQueryUsage | null>(() =>
    loadStoredUsage(),
  );
  const [result, setResult] = useState<OptimizeQueryResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  /**
   * Set once a config action was attempted, so an otherwise untyped (empty)
   * config also reveals its required fields instead of staying silent. Sticky:
   * it stays on until the config becomes valid, so the user can fix the fields
   * one by one and watch each message clear.
   */
  const [showConfigErrors, setShowConfigErrors] = useState(false);
  /**
   * Which fields the user has actually interacted with (edited or blurred).
   * Drives per-field feedback so typing into one field never lights up the rest.
   */
  const [touchedConfigFields, setTouchedConfigFields] =
    useState<DatabaseConfigTouched>({});
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("idle");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [optimizationPhase, setOptimizationPhase] = useState<string | null>(
    null,
  );

  const { mutateAsync: optimizeAsync, isPending: isOptimizing } =
    useOptimizeSqlQuery();
  const { mutateAsync: testAsync, isPending: isTestingConnection } =
    useTestDbConnection();
  const { data: usageData, refetch: refetchUsage } = useSqlOptimizerUsage();

  useEffect(() => {
    installDevLogConsole();
  }, []);

  useEffect(() => {
    if (
      usageData &&
      typeof usageData.queries_used === "number" &&
      typeof usageData.queries_remaining === "number"
    ) {
      const nextUsage: SqlQueryUsage = {
        queriesUsed: usageData.queries_used,
        queriesRemaining: usageData.queries_remaining,
        resetsIn: usageData.resets_in,
        limit: usageData.limit,
      };
      setUsage(nextUsage);
      persistUsage(nextUsage);
    }
  }, [usageData]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      persistOptimizerForm({
        databaseConfig,
        query,
        selectedScopeIds,
        customRequest,
      });
    }, 400);

    return () => window.clearTimeout(timeoutId);
  }, [databaseConfig, query, selectedScopeIds, customRequest]);

  /** Full-config validation result for the current connection details. */
  const configFieldErrors = useMemo(
    () => validateDatabaseConfigFields(databaseConfig),
    [databaseConfig],
  );
  /**
   * A complete config is required before either action can run: an ALL-EMPTY
   * config is incomplete too, so Test Connection and Optimize stay disabled
   * until every field is filled with valid details.
   */
  const isDatabaseConfigInvalid = useMemo(
    () => !isDatabaseConfigComplete(databaseConfig),
    [databaseConfig],
  );
  /**
   * Field-level red rings + messages are scoped to the fields the user has
   * actually touched, so editing Host never flags Port / User / Password /
   * Database. Attempting an action (Test Connection, Optimize, Ctrl+Enter)
   * reveals every outstanding error at once.
   */
  const databaseConfigErrors = useMemo(
    () =>
      showConfigErrors
        ? configFieldErrors
        : getVisibleDatabaseConfigErrors(configFieldErrors, touchedConfigFields),
    [configFieldErrors, showConfigErrors, touchedConfigFields],
  );
  /** Daily quota exhausted — the API would reject the request anyway. */
  const isQueryLimitReached = usage !== null && usage.queriesRemaining <= 0;
  /** Single gate for the Optimize action (button + Ctrl+Enter). */
  const canOptimize =
    !isOptimizing &&
    !isTestingConnection &&
    !isDatabaseConfigInvalid &&
    !isQueryLimitReached;

  const updateDatabaseField = useCallback(
    (field: keyof DatabaseConfig, value: string | boolean) => {
      setDatabaseConfig((prev) => {
        const next = { ...prev, [field]: value };
        return next;
      });
      // Editing a field is what makes it "touched" — only this field starts
      // reporting errors, the rest of the form stays quiet until visited.
      setTouchedConfigFields((prev) =>
        prev[field] ? prev : { ...prev, [field]: true },
      );
      setConnectionStatus("idle");
      setConnectionError(null);
    },
    [],
  );

  /**
   * Marks a field as visited when it loses focus, so tabbing into an empty
   * field and leaving it surfaces that field's own message.
   */
  const blurDatabaseField = useCallback((field: keyof DatabaseConfig) => {
    setTouchedConfigFields((prev) =>
      prev[field] ? prev : { ...prev, [field]: true },
    );
  }, []);

  const toggleScope = useCallback((scopeId: string) => {
    setSelectedScopeIds((prev) =>
      prev.includes(scopeId)
        ? prev.filter((id) => id !== scopeId)
        : [...prev, scopeId],
    );
  }, []);

  const saveCustomRequest = useCallback((request: string) => {
    setCustomRequest(request.trim());
  }, []);

  const updateQuery = useCallback((nextQuery: string) => {
    setQuery(nextQuery);
    setValidationError((prev) => {
      // Full re-validation once an error is already showing (so the user
      // sees it clear as they fix it)...
      if (prev !== null) return validateSqlQuery(nextQuery);
      // ...otherwise only the lightweight live guard, which flags
      // unambiguously destructive input (DELETE/DROP/...) instantly on paste
      // without nagging ordinary typing.
      return validateQueryGuard(nextQuery);
    });
  }, []);

  const applyUsageFromFailure = useCallback((failure: {
    limit?: number;
    queries_used?: number;
    queries_remaining?: number;
    reset_time?: string;
    resets_in?: number;
    message?: string;
  }) => {
    let used = failure.queries_used;
    let remaining = failure.queries_remaining;
    let limit = failure.limit;

    if (typeof used !== "number" || typeof remaining !== "number") {
      const match = failure.message?.match(/limit of (\d+) queries/i);
      if (match) {
        const parsedLimit = Number.parseInt(match[1], 10);
        if (Number.isFinite(parsedLimit) && parsedLimit > 0) {
          limit = limit ?? parsedLimit;
          used = parsedLimit;
          remaining = 0;
        }
      }
    }

    if (typeof used !== "number" || typeof remaining !== "number") {
      return;
    }
    const nextUsage: SqlQueryUsage = {
      queriesUsed: used,
      queriesRemaining: remaining,
      resetTime: failure.reset_time,
      resetsIn: failure.resets_in,
      limit,
    };
    setUsage(nextUsage);
    persistUsage(nextUsage);
  }, []);

  const testConnection = useCallback(async () => {
    if (isTestingConnection) return;

    const configError = getFirstDatabaseConfigError(
      validateDatabaseConfigFields(databaseConfig),
    );
    if (configError) {
      setShowConfigErrors(true);
      setConnectionError(configError);
      setConnectionStatus("failed");
      toastError(configError);
      return;
    }

    setConnectionError(null);
    setConnectionStatus("testing");

    const startedAt = Date.now();
    try {
      const response = await testAsync(databaseConfig);
      setConnectionStatus("connected");

      if (
        typeof response.queries_used === "number" &&
        typeof response.queries_remaining === "number"
      ) {
        const nextUsage: SqlQueryUsage = {
          queriesUsed: response.queries_used,
          queriesRemaining: response.queries_remaining,
          resetsIn: response.resets_in,
          limit: response.limit,
        };
        setUsage(nextUsage);
        persistUsage(nextUsage);
      } else {
        void refetchUsage();
      }

      const message =
        response.message?.trim() || "Database connection successful.";
      toastSuccess(message);
      emitDevLogRow({
        api: "db_optimize_test",
        db: dbLabel(databaseConfig),
        gotBack: message,
        connectionOk: "yes",
        optimized: "n/a",
        sameAsSql: "n/a",
        quotaLeft:
          typeof response.queries_remaining === "number"
            ? response.queries_remaining
            : null,
        tookMs: Date.now() - startedAt,
        problem: "none",
      });
    } catch (error) {
      setConnectionStatus("failed");
      if (error instanceof OptimizerRequestError) {
        applyUsageFromFailure(error.failure);
        setConnectionError(error.failure.message);
        showApiToast(error.failure.toast, error.failure.message, "error");
        emitDevLogRow({
          api: "db_optimize_test",
          db: dbLabel(databaseConfig),
          gotBack: error.failure.message,
          connectionOk: "no",
          optimized: "n/a",
          sameAsSql: "n/a",
          quotaLeft:
            typeof error.failure.queries_remaining === "number"
              ? error.failure.queries_remaining
              : null,
          tookMs: Date.now() - startedAt,
          problem: "minor",
        });
        return;
      }
      const message =
        error instanceof Error && error.message
          ? error.message
          : "Database connection failed.";
      setConnectionError(message);
      toastError(message);
      emitDevLogRow({
        api: "db_optimize_test",
        db: dbLabel(databaseConfig),
        gotBack: message,
        connectionOk: "no",
        optimized: "n/a",
        sameAsSql: "n/a",
        tookMs: Date.now() - startedAt,
        problem: "minor",
      });
    }
  }, [
    applyUsageFromFailure,
    databaseConfig,
    isTestingConnection,
    refetchUsage,
    testAsync,
  ]);

  const runOptimization = useCallback(async () => {
    if (isOptimizing) return;

    // Quota gate — the daily allowance is spent, so nothing can be optimized.
    if (isQueryLimitReached) {
      toastWarning(QUERY_LIMIT_REACHED_MESSAGE);
      return;
    }

    const trimmedQuery = query.trim();
    const sqlValidationError = validateSqlQuery(trimmedQuery);
    if (sqlValidationError) {
      setValidationError(sqlValidationError);
      setErrorMessage(null);
      return;
    }

    const configError = getFirstDatabaseConfigError(
      validateDatabaseConfigFields(databaseConfig),
    );
    if (configError) {
      setShowConfigErrors(true);
      setErrorMessage(configError);
      return;
    }

    if (connectionStatus !== "connected") {
      const msg =
        "Test the database connection before optimizing with live credentials.";
      setErrorMessage(msg);
      toastWarning(msg);
      return;
    }

    setErrorMessage(null);
    setValidationError(null);
    setResult(null);

    const startedAt = Date.now();
    const request: OptimizeQueryRequest = {
      query: trimmedQuery,
      scope: selectedScopeIds,
      custom_request: customRequest,
      database_config: databaseConfig,
    };

    try {
      const response = await optimizeAsync(request);
      setResult(response);

      if (
        typeof response.queries_used === "number" &&
        typeof response.queries_remaining === "number"
      ) {
        const nextUsage: SqlQueryUsage = {
          queriesUsed: response.queries_used,
          queriesRemaining: response.queries_remaining,
          resetTime: response.reset_time,
          resetsIn: response.resets_in,
          limit: response.limit,
        };
        setUsage(nextUsage);
        persistUsage(nextUsage);
      } else {
        void refetchUsage();
      }

      if (response.grounded === true) {
        toastSuccess(
          response.message ??
            "Optimized with live database schema & EXPLAIN.",
        );
      } else if (response.grounded === false) {
        toastWarning(
          response.message ??
            "Optimized with syntax only — live schema was not resolved.",
        );
      } else if (response.toast) {
        showApiToast(response.toast, response.message ?? "", "success");
      }

      if (response.error) {
        setErrorMessage(response.error);
      }

      emitDevLogRow({
        api: "db_optimize_full",
        query: trimmedQuery,
        db: dbLabel(databaseConfig),
        gotBack: response.summary?.[0] ?? response.message ?? "completed",
        optimized:
          response.optimized_query &&
          response.optimized_query.trim() !== "" &&
          response.optimized_query.trim() !== trimmedQuery
            ? "yes"
            : "no",
        sameAsSql: "n/a",
        connectionOk: response.grounded === true ? "yes" : "n/a",
        quotaLeft:
          typeof response.queries_remaining === "number"
            ? response.queries_remaining
            : null,
        tookMs: Date.now() - startedAt,
        problem: "none",
      });
    } catch (error) {
      if (error instanceof OptimizerRequestError) {
        applyUsageFromFailure(error.failure);
        setErrorMessage(error.failure.message);
        const toastType =
          error.failure.error_code === "RATE_LIMIT_EXCEEDED"
            ? "warning"
            : "error";
        showApiToast(error.failure.toast, error.failure.message, toastType);
        emitDevLogRow({
          api: "db_optimize_full",
          query: trimmedQuery,
          db: dbLabel(databaseConfig),
          gotBack: error.failure.message,
          optimized: "no",
          sameAsSql: "n/a",
          connectionOk: "n/a",
          quotaLeft:
            typeof error.failure.queries_remaining === "number"
              ? error.failure.queries_remaining
              : null,
          tookMs: Date.now() - startedAt,
          problem:
            error.failure.error_code === "RATE_LIMIT_EXCEEDED"
              ? "blocking"
              : "minor",
        });
        return;
      }

      const message = getOptimizeErrorMessage(error);
      setErrorMessage(message);
      toastError(message);
      emitDevLogRow({
        api: "db_optimize_full",
        query: trimmedQuery,
        db: dbLabel(databaseConfig),
        gotBack: message,
        optimized: "no",
        sameAsSql: "n/a",
        connectionOk: "n/a",
        tookMs: Date.now() - startedAt,
        problem: "blocking",
      });
    } finally {
      setOptimizationPhase(null);
    }
  }, [
    isOptimizing,
    isQueryLimitReached,
    query,
    databaseConfig,
    connectionStatus,
    selectedScopeIds,
    customRequest,
    optimizeAsync,
    refetchUsage,
    applyUsageFromFailure,
  ]);

  useEffect(() => {
    if (!isOptimizing) {
      setOptimizationPhase(null);
      return;
    }

    setOptimizationPhase(OPTIMIZATION_PHASES[0]);
    const phase2 = window.setTimeout(() => {
      setOptimizationPhase(OPTIMIZATION_PHASES[1]);
    }, 8000);
    const phase3 = window.setTimeout(() => {
      setOptimizationPhase(OPTIMIZATION_PHASES[2]);
    }, 45_000);

    return () => {
      window.clearTimeout(phase2);
      window.clearTimeout(phase3);
    };
  }, [isOptimizing]);

  const runOptimizationRef = useRef(runOptimization);
  useEffect(() => {
    runOptimizationRef.current = runOptimization;
  }, [runOptimization]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key !== "Enter") return;
      event.preventDefault();
      void runOptimizationRef.current();
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return {
    databaseConfig,
    updateDatabaseField,
    blurDatabaseField,
    query,
    setQuery: updateQuery,
    selectedScopeIds,
    toggleScope,
    customRequest,
    saveCustomRequest,
    usage,
    result,
    errorMessage,
    validationError,
    connectionStatus,
    connectionError,
    databaseConfigErrors,
    isDatabaseConfigInvalid,
    isTestingConnection,
    testConnection,
    optimizationPhase,
    isOptimizing,
    isQueryLimitReached,
    canOptimize,
    runOptimization,
    refetchUsage,
  };
}

export default useSqlOptimizer;
