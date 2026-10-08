/**
 * SOP Verification service.
 * POST /api/sop-verify — Generate Traffic-Light Review table from query verification data
 */

import { axiosInstance } from "../client/axios";
import { API_CONFIG } from "../../config/api.config";
import { SQL_OPTIMIZER_ENDPOINTS } from "../endpoints";
import { SopVerifyRequest, SopVerifyResponse } from "../types";

/**
 * Generate Traffic-Light Review table from query verification data.
 * This endpoint analyzes SQL query plans and generates a structured review.
 * 
 * @param request - The verification request with explain output, runtime data, and validation data
 * @returns Promise with the traffic-light table and summary
 */
export async function verifySopTable(request: SopVerifyRequest): Promise<SopVerifyResponse> {
  try {
    const response = await axiosInstance.post<Record<string, unknown>>(
      SQL_OPTIMIZER_ENDPOINTS.SOP_VERIFY.path,
      {
        explain_output: request.explain_output,
        runtime_data: request.runtime_data ?? {},
        validation_data: request.validation_data ?? {},
      },
      {
        baseURL: API_CONFIG.sqlOptimizerBaseUrl,
        timeout: API_CONFIG.sqlOptimizerTestTimeoutMs,
      },
    );

    const data = response.data;
    
    return {
      success: (data.success ?? false) as boolean,
      table: data.table as string | undefined,
      summary: data.summary as string | undefined,
      error: data.error as string | undefined,
    };
  } catch (error) {
    throw error;
  }
}