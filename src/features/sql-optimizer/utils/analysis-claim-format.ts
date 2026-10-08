/**
 * Splits a plan-analysis paragraph into per-claim bullets.
 *
 * The summarizer and planner stages return prose such as:
 *
 *   "The main query structure, including the `CROSS JOIN` and
 *    `LEFT JOIN InvoiceLine`, was refactored into CTEs ... ???
 *    This reduces the intermediate row count ... ??? ..."
 *
 * Rendered as one paragraph that is a wall of text. The boundaries between
 * claims are marked by the separator runs the stages emit (`???`, `---`) or
 * by sentence ends that start a new claim ("By ...ing", "Furthermore,",
 * "This ..."). This carves the text at those boundaries, so each line in the
 * UI is one self-contained claim.
 *
 * Guarantees (mirrors action-reason-format):
 *  - pure and React-free, unit-testable on its own;
 *  - nothing is ever dropped: text before the first boundary stays as the
 *    leading bullet, and text with no markers comes back whole;
 *  - no empty bullets: a boundary with no prose after it is skipped.
 */

/** One claim carved out of an analysis paragraph. */
export interface AnalysisClaim {
  /** The claim text, with boundary markers and extra whitespace removed. */
  body: string;
}

/**
 * Explicit claim separators emitted by the stages between claims.
 * In practice these arrive as multi-character runs ("???" in the reported
 * example); the run length varies, so two or more in a row is a boundary.
 */
const CLAIM_SEPARATOR = /(?:\?{2,}|-{2,}|…{1,}|\.{3,})/g;

/** Sentence ends that begin a new claim ("Furthermore, ...", "By ...ing ..."). */
const CLAIM_STARTERS =
  /\b(?:Furthermore|Moreover|Additionally|In addition|By extracting|By moving|By pre-aggregating|This (?:reduces|eliminates|avoids|moves|means|shows))\b/gi;

/** Minimum claim length after trimming — shorter fragments are not claims. */
const MIN_CLAIM_LENGTH = 12;

/** Collapse internal newlines/extra spaces so one claim renders on one line. */
function cleanClaim(text: string): string {
  return text.replace(/\s+/g, " ").replace(/^[:;\-–—\s]+/, "").trim();
}

/**
 * Carves `text` into one entry per claim it contains.
 *
 * Strategy, in order (each later step only runs when the earlier one found
 * nothing, so the earliest — most explicit — boundary always wins):
 *   1. explicit separators (`???`, `---`, …);
 *   2. claim-starter phrases ("Furthermore,", "By ...ing", "This reduces ...");
 *   3. plain sentences (last resort — still better than a wall of text).
 */
export function parseAnalysisClaims(text: string): AnalysisClaim[] {
  if (!text) {
    return [];
  }

  const claims: AnalysisClaim[] = [];
  const push = (body: string) => {
    const cleaned = cleanClaim(body);
    if (cleaned.length >= MIN_CLAIM_LENGTH) {
      claims.push({ body: cleaned });
    }
  };

  // 1. Explicit separators win outright.
  const separated = text.split(CLAIM_SEPARATOR).map((part) => part.trim()).filter(Boolean);
  if (separated.length > 1) {
    separated.forEach(push);
    return claims;
  }

  // 2. No separators — look for claim-starter phrases.
  const starters: number[] = [];
  for (const match of text.matchAll(CLAIM_STARTERS)) {
    starters.push(match.index ?? 0);
  }
  // The first starter only counts as a boundary if real prose precedes it;
  // otherwise the whole text is one claim and step 3 would split it wrongly.
  const meaningful = starters.filter((index) => cleanClaim(text.slice(0, index)).length >= MIN_CLAIM_LENGTH);
  if (meaningful.length > 0) {
    const boundaries = [0, ...meaningful];
    if (meaningful[meaningful.length - 1] < text.length) {
      boundaries.push(text.length);
    }
    for (let i = 0; i + 1 < boundaries.length; i++) {
      push(text.slice(boundaries[i], boundaries[i + 1]));
    }
    if (claims.length > 1) {
      return claims;
    }
    claims.length = 0;
  }

  // 3. Last resort: split into sentences. Single-sentence text yields exactly
  // one claim, which the caller renders identically to the old paragraph.
  const sentences = text.match(/[^.!?]+[.!?]+["'”’)]?|\S[^.!?]*$/g) ?? [];
  sentences.forEach(push);
  return claims;
}

/** True when the text should be rendered as a per-claim bullet list. */
export function hasAnalysisClaims(text: string): boolean {
  return parseAnalysisClaims(text).length > 1;
}
