/**
 * Optimization result cards.
 *
 * Three separate concerns, each in its own card:
 * - Summary          — what the optimizer says it changed (the existing narrative),
 * - Analysis & Explanation — per-action detail, including the EXPLAIN evidence
 *   behind each rewrite,
 * - Report           — run metadata, action outcomes and the original EXPLAIN plan.
 *
 * The "Actions" list is intentionally NOT repeated inside Summary any more: it
 * duplicated the Analysis section. Index recommendations stay in Summary since
 * they are DDL the user still has to run themselves.
 */

import { useCallback } from "react";
import ComponentCard from "../../../../components/common/component-card/ComponentCard";
import { toastError, toastSuccess } from "../../../../components/common/toast";
import Button from "../../../../components/ui/button/Button";
import { CopyIcon } from "../../../../icons";
import { copyToClipboard } from "../../../../utils/helpers";
import type { OptimizeQueryResponse } from "../../../../api/types";
import ActionAnalysisList from "./ActionAnalysisList";
import OptimizationReport from "./OptimizationReport";

const NO_CHANGES_MESSAGE =
  "No optimization changes were identified for this query.";

interface RecommendationListProps {
  title: string;
  items: string[];
}

const RecommendationList: React.FC<RecommendationListProps> = ({
  title,
  items,
}) => {
  return (
    <div className="border-t border-gray-100 pt-4 dark:border-gray-800">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
        {title}
      </h4>
      <ul className="list-disc space-y-1.5 pl-5 text-sm text-gray-600 dark:text-gray-300">
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>{item}</li>
        ))}
      </ul>
    </div>
  );
};

interface OptimizationResultProps {
  result: OptimizeQueryResponse;
}

const OptimizationResult: React.FC<OptimizationResultProps> = ({ result }) => {
  const lines = result.optimized_query.split("\n");
  const actionDetails = result.actionDetails ?? [];
  // Older/simpler responses only return the flattened action strings, so fall
  // back to them rather than dropping the section entirely.
  const hasAnalysis =
    actionDetails.length > 0 || result.actions.length > 0;

  const handleCopy = useCallback(async () => {
    const copied = await copyToClipboard(result.optimized_query);
    if (copied) {
      toastSuccess("Optimized query copied to clipboard.");
    } else {
      toastError("Unable to copy the optimized query.");
    }
  }, [result.optimized_query]);

  return (
    <div className="space-y-6">
      <ComponentCard title="Summary">
        {result.summary.length > 0 ? (
          <ul className="space-y-2 text-sm text-gray-600 dark:text-gray-300">
            {result.summary.map((item, index) => (
              <li key={`summary-${index}`}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {NO_CHANGES_MESSAGE}
          </p>
        )}

        {result.index_recommendations.length > 0 && (
          <RecommendationList
            title="Index Recommendations"
            items={result.index_recommendations}
          />
        )}
      </ComponentCard>

      {hasAnalysis && (
        <ComponentCard
          title="Analysis & Explanation"
          desc="Why each change was made, based on the live query plan."
        >
          {actionDetails.length > 0 ? (
            <ActionAnalysisList actions={actionDetails} />
          ) : (
            <RecommendationList title="Actions" items={result.actions} />
          )}
        </ComponentCard>
      )}

      <ComponentCard
        title="Report"
        desc="How this optimization ran, and the plan it was based on."
      >
        <OptimizationReport result={result} />
      </ComponentCard>

      <ComponentCard title="Optimized Query">
        <div className="relative rounded-xl border border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-white/[0.02]">
          <div className="absolute right-3 top-3">
            <Button
              variant="outline"
              size="sm"
              startIcon={<CopyIcon className="size-4" />}
              onClick={handleCopy}
            >
              Copy
            </Button>
          </div>

          <pre className="custom-scrollbar overflow-x-auto px-4 py-4 pr-28 font-mono text-sm leading-6 text-gray-800 dark:text-white/90">
            <code className="block">
              {lines.map((line, index) => (
                <span key={`line-${index}`} className="flex">
                  <span className="mr-4 w-6 shrink-0 select-none text-right text-gray-400 dark:text-gray-500">
                    {index + 1}
                  </span>
                  <span className="whitespace-pre">{line || " "}</span>
                </span>
              ))}
            </code>
          </pre>
        </div>
      </ComponentCard>
    </div>
  );
};

export default OptimizationResult;
