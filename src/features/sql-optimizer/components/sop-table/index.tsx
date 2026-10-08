/**
 * Traffic-Light Review SOP Table Component.
 * Displays query verification results in a traffic-light format.
 */

import ComponentCard from "../../../../components/common/component-card/ComponentCard";
import Button from "../../../../components/ui/button/Button";
import { CopyIcon } from "../../../../icons";
import { copyToClipboard } from "../../../../utils/helpers";
import { toastSuccess, toastError } from "../../../../components/common/toast";
import { SopVerifyResponse } from "../../../../api/types";
import SopReviewTable from "./SopReviewTable";
import { parseSopMarkdownTable, summarizeSopRows } from "./sop-table-parse";

interface SopTableProps {
  result?: SopVerifyResponse;
  isLoading?: boolean;
  error?: string;
}

const SopTable: React.FC<SopTableProps> = ({ result, isLoading, error }) => {
  const handleCopy = async () => {
    if (!result?.table) return;
    const copied = await copyToClipboard(result.table);
    if (copied) {
      toastSuccess("SOP Table copied to clipboard.");
    } else {
      toastError("Unable to copy the table.");
    }
  };

  if (isLoading) {
    return (
      <ComponentCard title="SOP Verification" desc="Analyzing query plan...">
        <div className="flex items-center justify-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-brand-500"></div>
        </div>
      </ComponentCard>
    );
  }

  if (error) {
    return (
      <ComponentCard title="SOP Verification" desc="Error occurred">
        <div className="text-sm text-error-600 dark:text-error-500">
          {error}
        </div>
      </ComponentCard>
    );
  }

  if (!result?.success || !result?.table) {
    return (
      <ComponentCard title="SOP Verification" desc="No results available">
        <div className="text-sm text-gray-500 dark:text-gray-400">
          No verification results to display.
        </div>
      </ComponentCard>
    );
  }

  const parsed = result.table ? parseSopMarkdownTable(result.table) : null;
  const lines = result.table.split("\n");
  const hasSummary = result.summary && result.summary.length > 0;
  const counts = parsed ? summarizeSopRows(parsed.rows) : null;
  const caption = counts
    ? `${counts.green}/${counts.total} checks green` +
      (counts.red > 0 ? ` · ${counts.red} need fixing` : "") +
      (counts.orange > 0 ? ` · ${counts.orange} to investigate` : "")
    : undefined;

  return (
    <div className="space-y-6">
      <ComponentCard title="Traffic-Light Review Table" desc={caption ?? "Query plan analysis results"}>
        <div className="relative rounded-xl border border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-white/[0.02]">
          <div className="absolute right-3 top-3 z-10">
            <Button
              variant="outline"
              size="sm"
              startIcon={<CopyIcon className="size-4" />}
              onClick={handleCopy}
              disabled={isLoading}
            >
              Copy
            </Button>
          </div>
          {parsed ? (
            <div className="px-4 py-4 pr-4 pt-14">
              <SopReviewTable rows={parsed.rows} />
            </div>
          ) : (
          <div className="px-4 py-4 overflow-x-auto">
            <pre className="font-mono text-xs sm:text-sm leading-6 text-gray-800 dark:text-white/90">
              {lines.map((line, index) => (
                <span key={`line-${index}`} className="flex">
                  {index === 0 ? (
                    <span className="font-bold text-brand-600 dark:text-brand-400">{line}</span>
                  ) : (
                    <>
                      <span className="w-6 shrink-0 select-none text-right text-gray-400 dark:text-gray-500 text-xs">
                        {line.trim().length > 0 ? index + 1 : ""}
                      </span>
                      <span className="whitespace-pre">{line || " "}</span>
                    </>
                  )}
                </span>
              ))}
            </pre>
          </div>
          )}
        </div>
      </ComponentCard>

      {hasSummary && (
        <ComponentCard
          title="Summary"
          desc="Overall assessment of the query plan"
        >
          <div className="text-sm text-gray-600 dark:text-gray-300 space-y-2">
            <p>{result.summary}</p>
          </div>
        </ComponentCard>
      )}
    </div>
  );
};

export default SopTable;