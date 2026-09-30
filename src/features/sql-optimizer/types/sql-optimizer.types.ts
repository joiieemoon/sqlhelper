/**
 * SQL optimizer feature types.
 * UI-facing types for the query optimizer page.
 */

/** A selectable "OPTIMIZE FOR" scope. Sent to the API as a scope value. */
export interface OptimizeScope {
  id: string;
  label: string;
}

/** Database connection details sent to the optimizer API. */
export interface DatabaseConfig {
  host: string;
  port: string;
  user: string;
  password: string;
  database: string;
  ssl_verify: boolean;
}

export type ConnectionStatus = "idle" | "testing" | "connected" | "failed";

/** Editable database connection fields (everything except the ssl_verify toggle). */
export type DatabaseConfigField = keyof DatabaseConfig;

/** Per-field validation messages, keyed by the config field they belong to. */
export type DatabaseConfigErrors = Partial<Record<DatabaseConfigField, string>>;

/**
 * Config fields the user has actually interacted with — edited (onChange) or
 * focused-then-left (onBlur). Mirrors Formik's `touched` map, which the rest of
 * the project already uses to show a field's error only after that field was
 * visited.
 */
export type DatabaseConfigTouched = Partial<Record<DatabaseConfigField, boolean>>;

/** Optimizer quota reported by the API after each optimization or connection test. */
export interface SqlQueryUsage {
  queriesUsed: number;
  queriesRemaining: number;
  resetTime?: string;
  resetsIn?: number;
  limit?: number;
}
