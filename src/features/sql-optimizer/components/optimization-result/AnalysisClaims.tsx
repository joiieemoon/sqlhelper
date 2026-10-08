/**
 * AnalysisClaims — renders a plan-analysis paragraph as one bullet per claim.
 *
 * Long LLM prose (summarizer `summary`, action `reason` without issue markers)
 * arrives as a run-on paragraph; this splits it at claim boundaries (see
 * analysis-claim-format) and renders disc bullets. Text that is already a
 * single claim renders as the original plain paragraph, so the change is
 * purely visual and never drops or reorders content.
 */

import { hasAnalysisClaims, parseAnalysisClaims } from "../../utils/analysis-claim-format";

interface AnalysisClaimsProps {
  text: string;
  /** Extra classes for the paragraph fallback. */
  className?: string;
  /**
   * When true the single-claim fallback also renders as a one-item bullet
   * list, keeping list styling consistent inside `<ul>` parents.
   */
  bulletFallback?: boolean;
}

const AnalysisClaims: React.FC<AnalysisClaimsProps> = ({
  text,
  className = "text-sm leading-relaxed text-gray-600 dark:text-gray-300",
  bulletFallback = false,
}) => {
  const claims = parseAnalysisClaims(text);

  if (claims.length === 0) {
    return <p className={className}>{text}</p>;
  }

  if (claims.length === 1 && !bulletFallback) {
    return <p className={className}>{claims[0].body}</p>;
  }

  return (
    <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-gray-600 dark:text-gray-300">
      {claims.map((claim, index) => (
        <li key={`analysis-claim-${index}`}>{claim.body}</li>
      ))}
    </ul>
  );
};

export default AnalysisClaims;
