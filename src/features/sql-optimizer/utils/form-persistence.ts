/**
 * Persists optimizer form fields in localStorage for local/testing convenience.
 * Password is never stored — re-enter it each session.
 */

import storage from "../../../utils/storage";
import {
  DEFAULT_DATABASE_CONFIG,
  DEFAULT_SELECTED_SCOPE_IDS,
  DEFAULT_SQL_QUERY,
} from "../constants/sql-optimizer.constants";
import type { DatabaseConfig } from "../types/sql-optimizer.types";

export const OPTIMIZER_FORM_STORAGE_KEY = "sql-optimizer-form";

/** Database fields saved to localStorage (password excluded). */
export interface StoredDatabaseConfig {
  host: string;
  port: string;
  user: string;
  database: string;
  ssl_verify: boolean;
}

export interface StoredOptimizerForm {
  databaseConfig: DatabaseConfig;
  query: string;
  selectedScopeIds: string[];
  customRequest: string;
}

interface PersistedOptimizerForm {
  databaseConfig: StoredDatabaseConfig;
  query: string;
  selectedScopeIds: string[];
  customRequest: string;
}

function isStoredDatabaseConfig(value: unknown): value is StoredDatabaseConfig {
  if (!value || typeof value !== "object") {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.host === "string" &&
    typeof record.port === "string" &&
    typeof record.user === "string" &&
    typeof record.database === "string" &&
    typeof record.ssl_verify === "boolean"
  );
}

function toDatabaseConfig(stored: StoredDatabaseConfig): DatabaseConfig {
  return {
    ...stored,
    password: "",
  };
}

/** Removes legacy password field from an older saved form, if present. */
function scrubStoredPasswordFromDisk(): void {
  const raw = storage.getItem<PersistedOptimizerForm & { databaseConfig?: Record<string, unknown> }>(
    OPTIMIZER_FORM_STORAGE_KEY,
  );
  if (!raw?.databaseConfig || !("password" in raw.databaseConfig)) {
    return;
  }

  const rest = { ...raw.databaseConfig };
  delete rest.password;
  if (!isStoredDatabaseConfig(rest)) {
    return;
  }

  storage.setItem<PersistedOptimizerForm>(OPTIMIZER_FORM_STORAGE_KEY, {
    databaseConfig: rest,
    query: typeof raw.query === "string" ? raw.query : DEFAULT_SQL_QUERY,
    selectedScopeIds: Array.isArray(raw.selectedScopeIds)
      ? raw.selectedScopeIds.filter((id) => typeof id === "string")
      : [...DEFAULT_SELECTED_SCOPE_IDS],
    customRequest:
      typeof raw.customRequest === "string" ? raw.customRequest : "",
  });
}

export function loadStoredOptimizerForm(): StoredOptimizerForm {
  scrubStoredPasswordFromDisk();

  const stored = storage.getItem<PersistedOptimizerForm>(
    OPTIMIZER_FORM_STORAGE_KEY,
  );

  return {
    databaseConfig: isStoredDatabaseConfig(stored?.databaseConfig)
      ? toDatabaseConfig(stored.databaseConfig)
      : { ...DEFAULT_DATABASE_CONFIG },
    query:
      typeof stored?.query === "string" ? stored.query : DEFAULT_SQL_QUERY,
    selectedScopeIds: Array.isArray(stored?.selectedScopeIds)
      ? stored.selectedScopeIds.filter((id) => typeof id === "string")
      : [...DEFAULT_SELECTED_SCOPE_IDS],
    customRequest:
      typeof stored?.customRequest === "string" ? stored.customRequest : "",
  };
}

export function persistOptimizerForm(form: StoredOptimizerForm): void {
  const databaseConfig: StoredDatabaseConfig = {
    host: form.databaseConfig.host,
    port: form.databaseConfig.port,
    user: form.databaseConfig.user,
    database: form.databaseConfig.database,
    ssl_verify: form.databaseConfig.ssl_verify,
  };

  storage.setItem<PersistedOptimizerForm>(OPTIMIZER_FORM_STORAGE_KEY, {
    databaseConfig,
    query: form.query,
    selectedScopeIds: form.selectedScopeIds,
    customRequest: form.customRequest,
  });
}

let initialFormCache: StoredOptimizerForm | null = null;

/** Reads localStorage once per page load for initial React state. */
export function getInitialOptimizerForm(): StoredOptimizerForm {
  if (!initialFormCache) {
    initialFormCache = loadStoredOptimizerForm();
  }
  return initialFormCache;
}
