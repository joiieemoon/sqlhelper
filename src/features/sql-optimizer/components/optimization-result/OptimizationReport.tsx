/**
 * "Report" section — the run metadata and diagnostics behind an optimization.
 *
 * Separated from the Summary so the narrative ("what changed and why") and the
 * facts ("how it ran, on what, with what plan") do not compete for the same
 * card. Every block is optional: the API omits what it did not compute, and an
 * empty report renders a single "no report data" line rather than empty boxes.
 */

import type { OptimizeQueryResponse } from "../../../../api/types";

const NO_REPORT_DATA_MESSAGE =
  "No report data was returned for this optimization.";

/** Action-id buckets reported by the API, in display order. */
const ACTION_BUCKETS = [
  { key: "applied_actions", label: "Applied" },
  { key: "rewrite_actions", label: "Rewritten" },
  { key: "no_op_actions", label: "No-op" },
  { key: "skipped_actions", label: "Skipped" },
] as const;

/** Formats a duration for the report, e.g. 98041 → "1m 38s". */
function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

interface ReportStat {
  label: string;
  value: string;
}

interface OptimizationReportProps {
  result: OptimizeQueryResponse;
}

const OptimizationReport: React.FC<OptimizationReportProps> = ({ result }) => {
  const explainPlan = result.explain?.rows
    ?.map((row) => row.EXPLAIN)
    .filter((plan): plan is string => Boolean(plan))
    .join("\n");

  const stats: ReportStat[] = [];
  if (result.engine) {
    stats.push({ label: "Engine", value: result.engine });
  }
  if (result.database) {
    stats.push({ label: "Database", value: result.database });
  }
  if (typeof result.elapsed_ms === "number") {
    stats.push({ label: "Duration", value: formatElapsed(result.elapsed_ms) });
  }
  if (typeof result.query_changed === "boolean") {
    stats.push({
      label: "Query changed",
      value: result.query_changed ? "Yes" : "No",
    });
  }

  const actionCounts = ACTION_BUCKETS.map(({ key, label }) => ({
    label,
    count: result[key]?.length ?? 0,
    present: Array.isArray(result[key]),
  })).filter((bucket) => bucket.present);

  const hasContent =
    stats.length > 0 ||
    actionCounts.length > 0 ||
    Boolean(explainPlan) ||
    Boolean(result.rewriter_warnings && result.rewriter_warnings.length > 0);

  return (
    <div className="space-y-4">
      {!hasContent && (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {NO_REPORT_DATA_MESSAGE}
        </p>
      )}

      {stats.length > 0 && (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {stats.map((stat) => (
            <div
              key={stat.label}
              className="rounded-xl border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-white/[0.02]"
            >
              <dt className="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                {stat.label}
              </dt>
              <dd className="mt-1 text-sm font-semibold text-gray-800 dark:text-white/90">
                {stat.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {actionCounts.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            Action outcomes
          </h4>
          <div className="flex flex-wrap gap-2">
            {actionCounts.map((bucket) => (
              <span
                key={bucket.label}
                className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 dark:bg-white/[0.06] dark:text-gray-200"
              >
                {bucket.label}: {bucket.count}
              </span>
            ))}
          </div>
        </div>
      )}

      {explainPlan && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            EXPLAIN plan (original query)
          </h4>
          <pre className="custom-scrollbar overflow-x-auto rounded-xl bg-gray-900 p-4 font-mono text-xs leading-5 text-gray-100">
            <code className="block whitespace-pre">{explainPlan}</code>
          </pre>
        </div>
      )}

      {result.rewriter_warnings && result.rewriter_warnings.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            Rewriter warnings
          </h4>
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-gray-600 dark:text-gray-300">
            {result.rewriter_warnings.map((warning, index) => (
              <li key={`rewriter-warning-${index}`}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

export default OptimizationReport;