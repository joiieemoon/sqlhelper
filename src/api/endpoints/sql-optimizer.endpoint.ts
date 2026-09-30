/**
 * SQL optimizer endpoints.
 * Centralizes all query optimizer API endpoint paths.
 */

import { HttpMethod } from "../types";

/**
 * SQL optimizer API endpoints.
 * Hosted on its own base URL (see API_CONFIG.sqlOptimizerBaseUrl).
 */
export const SQL_OPTIMIZER_ENDPOINTS = {
  USAGE: {
    path: "/usage",
    method: HttpMethod.GET,
    requiresAuth: false,
  } as const,
  DB_OPTIMIZE_TEST: {
    path: "/db_optimize_test",
    method: HttpMethod.POST,
    requiresAuth: false,
  } as const,
  DB_OPTIMIZE_FULL: {
    path: "/db_optimize_full",
    method: HttpMethod.POST,
    requiresAuth: false,
  } as const,
} as const;

export type SqlOptimizerEndpoint =
  (typeof SQL_OPTIMIZER_ENDPOINTS)[keyof typeof SQL_OPTIMIZER_ENDPOINTS];
