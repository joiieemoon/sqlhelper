/**
 * SQL optimizer usage query hook.
 * Fetches authoritative quota and usage from GET /usage without side-effects.
 */

import { useQuery } from "@tanstack/react-query";
import { getOptimizerUsage } from "../services";
import { SQL_OPTIMIZER_QUERY_KEYS } from "../query";
import { SqlOptimizerUsageResponse } from "../types";

export function useSqlOptimizerUsage(options?: { enabled?: boolean }) {
  return useQuery<SqlOptimizerUsageResponse, Error>({
    queryKey: SQL_OPTIMIZER_QUERY_KEYS.USAGE(),
    queryFn: async () => getOptimizerUsage(),
    staleTime: 30 * 1000,
    gcTime: 5 * 60 * 1000,
    retry: 1,
    refetchOnWindowFocus: false,
    enabled: options?.enabled ?? true,
  });
}

export default useSqlOptimizerUsage;
