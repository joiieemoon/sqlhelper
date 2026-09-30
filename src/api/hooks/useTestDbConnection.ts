/**
 * Test database connection mutation hook.
 */

import { useMutation } from "@tanstack/react-query";
import { testDatabaseConnection } from "../services";
import { SQL_OPTIMIZER_MUTATION_KEYS } from "../query";
import { DatabaseConfig, DbTestConnectionResponse } from "../types";

export function useTestDbConnection() {
  return useMutation<DbTestConnectionResponse, Error, DatabaseConfig>({
    mutationKey: SQL_OPTIMIZER_MUTATION_KEYS.TEST_CONNECTION(),
    mutationFn: async (databaseConfig) =>
      testDatabaseConnection(databaseConfig),
    retry: 0,
  });
}

export default useTestDbConnection;
