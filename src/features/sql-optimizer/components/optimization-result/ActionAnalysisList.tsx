/**
 * Per-action "Analysis & Explanation" cards.
 *
 * The live pipeline returns `actions` as objects whose `reason` explains the
 * rewrite in terms of the actual EXPLAIN evidence. The Summary section only
 * carries the flattened "TYPE: reason" one-liners, so this renders each action
 * as its own entry with the metadata the optimizer reported.
 *
 * A reason covering several merged issues arrives as one run-on paragraph, so
 * it is split into a labelled bullet per issue (see action-reason-format).
 */

import type { OptimizerAction } from "../../../../api/types";
import { parseActionReasonIssues } from "../../utils/action-reason-format";
import AnalysisClaims from "./AnalysisClaims";

/** Human label for a machine action code, e.g. PUSH_DOWN_AGGREGATION. */
function humanizeActionType(type: string): string {
  return type
    .toLowerCase()
    .split("_")
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/**
 * An action's `reason`.
 *
 * One action can resolve several issues at once, and the planner then writes a
 * single run-on paragraph prefixed with "Issue 1 (CODE): ...", "Issue 2
 * (CODE): ...". Rendering that as one paragraph buries each issue inside the
 * next, so when markers are present the text is split into a labelled bullet
 * per issue. A reason with no markers keeps the original single-paragraph form.
 */
const ActionReason: React.FC<{ reason: string }> = ({ reason }) => {
  const issues = parseActionReasonIssues(reason);

  if (issues.length === 0) {
    // No "Issue N (CODE):" markers — fall back to per-claim bullets so a
    // run-on reason still reads line by line. A single claim renders as the
    // original paragraph, so this is purely visual.
    return <AnalysisClaims text={reason} />;
  }

  return (
    <ul className="space-y-2 text-sm leading-relaxed text-gray-600 dark:text-gray-300">
      {issues.map((issue, index) => {
        // Unlabelled preamble (text before the first marker) stays a plain
        // bullet so no part of the reason is dropped.
        const label = issue.issueCode
          ? `Issue ${issue.issueNumber} · ${issue.issueCode}`
          : "";

        return (
          <li key={`${issue.issueCode}-${issue.issueNumber}-${index}`}>
            {label && (
              <span className="mr-1.5 inline-flex items-baseline rounded-md bg-gray-100 px-1.5 py-0.5 font-mono text-xs font-medium text-gray-700 dark:bg-white/[0.06] dark:text-gray-200">
                {label}
              </span>
            )}
            <span>{issue.body}</span>
          </li>
        );
      })}
    </ul>
  );
};

interface ActionAnalysisListProps {
  actions: OptimizerAction[];
}

const ActionAnalysisList: React.FC<ActionAnalysisListProps> = ({ actions }) => {
  if (actions.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4">
      {actions.map((action, index) => {
        const title = action.type ? humanizeActionType(action.type) : "Optimization";
        // Key on the action id when present so re-renders stay stable.
        const key = action.id ?? `action-${index}`;

        return (
          <article
            key={key}
            className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-800 dark:bg-white/[0.02]"
          >
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h5 className="text-sm font-semibold text-gray-800 dark:text-white/90">
                {title}
              </h5>
              {action.priority !== undefined && (
                <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 dark:bg-brand-500/10 dark:text-brand-300">
                  Priority {action.priority}
                </span>
              )}
              {action.table && (
                <span className="rounded-full bg-gray-100 px-2 py-0.5 font-mono text-xs text-gray-600 dark:bg-white/[0.06] dark:text-gray-300">
                  {action.table}
                </span>
              )}
              {action.columns && action.columns.length > 0 && (
                <span className="rounded-full bg-gray-100 px-2 py-0.5 font-mono text-xs text-gray-600 dark:bg-white/[0.06] dark:text-gray-300">
                  {action.columns.join(", ")}
                </span>
              )}
            </div>

            {action.reason && <ActionReason reason={action.reason} />}

            {action.ddl && (
              <pre className="custom-scrollbar mt-3 overflow-x-auto rounded-lg bg-gray-900 p-3 font-mono text-xs leading-5 text-gray-100">
                <code className="block">{action.ddl}</code>
              </pre>
            )}
          </article>
        );
      })}
    </div>
  );
};

export default ActionAnalysisList;