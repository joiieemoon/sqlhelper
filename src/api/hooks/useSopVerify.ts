/**
 * Hook for SOP verification.
 * Provides state management for Traffic-Light Review table generation.
 */

import { useMutation } from "@tanstack/react-query";
import { verifySopTable } from "../services/sop-verify.service";
import { SQL_OPTIMIZER_ENDPOINTS } from "../endpoints";
import { SopVerifyRequest, SopVerifyResponse } from "../types";

/**
 * Use SOP verification hook.
 * 
 * @returns Mutation for SOP verification
 */
export function useSopVerify() {
  const mutation = useMutation<SopVerifyResponse, Error, SopVerifyRequest>({
    mutationFn: verifySopTable,
    mutationKey: [SQL_OPTIMIZER_ENDPOINTS.SOP_VERIFY.path],
  });

  return {
    mutate: mutation.mutate,
    isVerifying: mutation.isPending,
    sopTableResult: mutation.data,
    sopError: mutation.error?.message,
  };
}