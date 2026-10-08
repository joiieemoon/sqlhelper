/**
 * Page header for the standalone query optimizer page.
 * Full-width bar with the SQL badge, page title and the shared theme toggle.
 */

import { ThemeToggleButton } from "../../../../components/common/theme-toggle-button/ThemeToggleButton";
import type { SqlQueryUsage } from "../../types/sql-optimizer.types";
import { QUERY_LIMIT } from "../../constants/sql-optimizer.constants";

interface OptimizerHeaderProps {
  usage: SqlQueryUsage | null;
}

function formatResetsIn(seconds: number): string {
  if (seconds <= 0) return "soon";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0 && minutes > 0) return `${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h`;
  if (minutes > 0) return `${minutes}m`;
  return `${seconds}s`;
}

function formatResetTime(isoOrDate: string): string {
  try {
    const d = new Date(isoOrDate);
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }
  } catch {
    // fallback to string verbatim
  }
  return isoOrDate;
}

/**
 * Turns the usage payload into the header line.
 *
 * The API reports whatever RATE_LIMIT_MAX the server booted with, and that is
 * deliberately raised for local/test runs (the backend default of 5 is
 * overridden by that env var). Displaying the raw server figure leaks a dev
 * artifact into the UI — "99996 of 100000 remaining today" is meaningless to
 * anyone reading the product.
 *
 * So the header always reports the product allowance (QUERY_LIMIT) and counts
 * down within it. A server limit STRICTER than the product allowance is still
 * honoured, so a reduced or trial quota reports truthfully instead of being
 * rounded up.
 */
function formatUsage(usage: SqlQueryUsage | null): string {
  if (!usage) {
    return `Up to ${QUERY_LIMIT} queries per day`;
  }

  const serverLimit =
    usage.limit ?? usage.queriesUsed + usage.queriesRemaining;
  const limit =
    serverLimit > 0 && serverLimit < QUERY_LIMIT ? serverLimit : QUERY_LIMIT;
  // Never claim more than the reported allowance is left.
  const remaining = Math.max(0, Math.min(usage.queriesRemaining, limit));

  let resetHint = "";
  if (typeof usage.resetsIn === "number" && usage.resetsIn > 0) {
    resetHint = ` · resets in ${formatResetsIn(usage.resetsIn)}`;
  } else if (usage.resetTime) {
    resetHint = ` · resets ${formatResetTime(usage.resetTime)}`;
  }

  return `${remaining} of ${limit} remaining today${resetHint}`;
}

const OptimizerHeader: React.FC<OptimizerHeaderProps> = ({ usage }) => {
  return (
    <header className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-4 border-b border-gray-200 bg-white px-5 py-3 dark:border-gray-800 dark:bg-gray-900">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-500 text-[11px] font-bold tracking-wide text-white shadow-theme-xs">
          SQL
        </span>
        <div>
          <h1 className="text-xl font-semibold text-gray-800 dark:text-white/90">
            SQL Helper
          </h1>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {formatUsage(usage)}
          </p>
        </div>
      </div>

      <ThemeToggleButton />
    </header>
  );
};

export default OptimizerHeader;
