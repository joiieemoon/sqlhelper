/**
 * SQL Query Optimizer page.
 * Database configuration panel on the left, query workspace on the right.
 */

import { Alert } from "../../components/common/alert";
import PageMeta from "../../components/common/pagemeta/PageMeta";
import Button from "../../components/ui/button/Button";
import { BoltIcon } from "../../icons";
import DatabaseConfigPanel from "./components/database-config-panel/DatabaseConfigPanel";
import OptimizationResult from "./components/optimization-result/OptimizationResult";
import OptimizeForSelector from "./components/optimize-for-selector/OptimizeForSelector";
import OptimizerHeader from "./components/optimizer-header/OptimizerHeader";
import SqlQueryEditor from "./components/sql-query-editor/SqlQueryEditor";
import {
  OPTIMIZE_SCOPES,
  OPTIMIZE_SHORTCUT_LABEL,
  QUERY_LIMIT_REACHED_MESSAGE,
} from "./constants/sql-optimizer.constants";
import { useSqlOptimizer } from "./hooks/useSqlOptimizer";

const OptimizeSpinner: React.FC = () => (
  <svg
    className="animate-spin h-4 w-4"
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    viewBox="0 0 24 24"
  >
    <circle
      className="opacity-25"
      cx="12"
      cy="12"
      r="10"
      stroke="currentColor"
      strokeWidth="4"
    />
    <path
      className="opacity-75"
      fill="currentColor"
      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
    />
  </svg>
);

export default function SqlOptimizer() {
  const {
    databaseConfig,
    updateDatabaseField,
    blurDatabaseField,
    query,
    setQuery,
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
  } = useSqlOptimizer();

  const showGroundingWarning = result?.grounded === false;

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <PageMeta
        title="Query Optimizer | SQL Helper"
        description="Optimize SQL queries with SQL Helper"
      />

      <OptimizerHeader usage={usage} />

      <div className="mx-auto w-full max-w-(--breakpoint-2xl) p-4 md:p-6">
        <div className="grid grid-cols-12 gap-4 md:gap-6">
          <div className="col-span-12 xl:col-span-4 2xl:col-span-3">
            <DatabaseConfigPanel
              config={databaseConfig}
              configErrors={databaseConfigErrors}
              isConfigInvalid={isDatabaseConfigInvalid}
              onChange={updateDatabaseField}
              onBlurField={blurDatabaseField}
              onTestConnection={() => void testConnection()}
              isTestingConnection={isTestingConnection}
              connectionStatus={connectionStatus}
              connectionError={connectionError}
            />
          </div>

          <div className="col-span-12 space-y-6 xl:col-span-8 2xl:col-span-9">
            <SqlQueryEditor
              label="SQL Query"
              value={query}
              onChange={setQuery}
              error={validationError}
            />

            <div className="space-y-4">
              <OptimizeForSelector
                scopes={OPTIMIZE_SCOPES}
                selectedScopeIds={selectedScopeIds}
                onToggleScope={toggleScope}
                customRequest={customRequest}
                onSaveCustomRequest={saveCustomRequest}
              />

              <div className="flex flex-wrap items-center gap-3">
                <Button
                  onClick={() => void runOptimization()}
                  disabled={!canOptimize}
                  startIcon={isOptimizing ? <OptimizeSpinner /> : <BoltIcon />}
                  className="px-6 py-3.5 text-sm font-semibold"
                >
                  {isOptimizing ? "Optimizing..." : "Optimize Query"}
                </Button>
                {isQueryLimitReached ? (
                  <span className="text-xs font-medium text-error-600 dark:text-error-500">
                    {QUERY_LIMIT_REACHED_MESSAGE}
                  </span>
                ) : (
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    {OPTIMIZE_SHORTCUT_LABEL}
                  </span>
                )}
              </div>

              {isOptimizing && optimizationPhase && (
                <p className="text-xs text-brand-600 dark:text-brand-400">
                  {optimizationPhase}
                </p>
              )}
            </div>

            {errorMessage && (
              <Alert
                variant="error"
                title="Optimization failed"
                description={errorMessage}
                closable
              />
            )}

            {showGroundingWarning && (
              <Alert
                variant="warning"
                title="Syntax-only optimization"
                description={
                  result.message ??
                  "Live database schema was not resolved for this query."
                }
                closable
              />
            )}

            {result?.schema_warnings && result.schema_warnings.length > 0 && (
              <Alert
                variant="warning"
                title="Schema warnings"
                description={result.schema_warnings.join(" ")}
                closable
              />
            )}

            {result && <OptimizationResult result={result} />}
          </div>
        </div>
      </div>
    </div>
  );
}
