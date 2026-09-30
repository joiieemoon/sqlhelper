/**
 * API configuration.
 * Centralizes all API-related settings.
 */

function parsePositiveIntEnv(value: unknown, fallback: number): number {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/** Default 7 minutes — LLM + EXPLAIN pipeline can run several minutes. */
const DEFAULT_SQL_OPTIMIZER_TIMEOUT_MS = 7 * 60 * 1000;

export const API_CONFIG = {
  baseUrl: import.meta.env.VITE_API_BASE_URL || "https://dummyjson.com",
  /** Query optimizer API host (different host from the main API). */
  sqlOptimizerBaseUrl:
    import.meta.env.VITE_SQL_OPTIMIZER_API_BASE_URL ||
    "https://nv8vvk50-8000.inc1.devtunnels.ms",
  /** POST /db_optimize_full — override with VITE_SQL_OPTIMIZER_TIMEOUT_MS. */
  sqlOptimizerTimeoutMs: parsePositiveIntEnv(
    import.meta.env.VITE_SQL_OPTIMIZER_TIMEOUT_MS,
    DEFAULT_SQL_OPTIMIZER_TIMEOUT_MS,
  ),
  /** Connection test should fail fast. */
  sqlOptimizerTestTimeoutMs: parsePositiveIntEnv(
    import.meta.env.VITE_SQL_OPTIMIZER_TEST_TIMEOUT_MS,
    30_000,
  ),
  timeout: 30000,
  logging: import.meta.env.VITE_API_LOGGING === "true",
};

export type ApiConfig = typeof API_CONFIG;
