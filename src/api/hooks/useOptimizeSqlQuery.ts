/**
 * Optimize SQL query mutation hook.
 * Provides a typed mutation for the query optimizer API.
 */

import { useMutation } from "@tanstack/react-query";
import { optimizeSqlQuery } from "../services";
import { SQL_OPTIMIZER_MUTATION_KEYS } from "../query";
import { OptimizeQueryRequest, OptimizeQueryResponse } from "../types";

/**
 * Optimize SQL query mutation hook.
 * Retries are disabled: the endpoint is quota-limited, so a failed attempt
 * must not silently consume another query.
 */
export function useOptimizeSqlQuery() {
  return useMutation<OptimizeQueryResponse, Error, OptimizeQueryRequest>({
    mutationKey: SQL_OPTIMIZER_MUTATION_KEYS.OPTIMIZE(),
    mutationFn: async (request: OptimizeQueryRequest) => {
      const response = await optimizeSqlQuery(request);
      return response;
    },
    retry: 0,
  });
}

export default useOptimizeSqlQuery;
