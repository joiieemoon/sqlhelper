/**
 * Splits a planner action's `reason` into per-issue bullets.
 *
 * When one action resolves several issues (the planner merges overlapping ones
 * into a single action with an `issue_id` array), the reason arrives as one
 * run-on paragraph like:
 *
 *   "Issue 1 (ROW_EXPLOSION): the LEFT JOIN ... mitigating ROW_EXPLOSION.
 *    Issue 2 (TEMP_TABLE_COST): the original query used COUNT(...) ..."
 *
 * As one paragraph that is a wall of text where the reader loses track of
 * which claim belongs to which problem. These bullets give each issue its own
 * labelled line, so the evidence stays tied to the issue it justifies.
 *
 * Pure and React-free so it can be unit tested on its own.
 */

/** One "Issue N (CODE): ..." segment carved out of a reason string. */
export interface ActionReasonIssue {
  /** The issue number as the planner wrote it, e.g. 1. */
  issueNumber: string;
  /** The machine cost code, e.g. ROW_EXPLOSION. */
  issueCode: string;
  /** The prose explaining this issue, with the marker removed. */
  body: string;
}

/**
 * Matches an issue marker, plus the decoration the planner wraps around it.
 *
 * In practice the planner writes a numbered markdown list:
 *
 *   1. **Issue 2 (TEMP_TABLE_COST):** The original query performed ...
 *
 * so the list number, the bold markers and the trailing `**` are all consumed
 * here. Leaving them in would leak literal `**` and a stray "3." into the
 * rendered bullet. Both the decorated and the bare "Issue 2 (CODE):" forms are
 * accepted, and matching is case-insensitive because the model's casing varies.
 *
 * The code is matched as an uppercase word so ordinary parenthetical prose —
 * "(one row per TrackId)" — is never mistaken for a marker.
 */
const ISSUE_MARKER =
  /(?:\d+\.\s*)?\**\s*Issue\s+(\d+)\s*\(\s*([A-Z][A-Z0-9_]*)\s*\)\s*:\s*\**/gi;

/**
 * Carves `reason` into one entry per issue it mentions.
 *
 * Returns an empty array when the text has no recognisable issue markers, in
 * which case the caller should render the reason as a single paragraph rather
 * than showing an empty or partial list. Text before the first marker is kept
 * as a leading entry with no issue label so nothing is silently dropped.
 */
export function parseActionReasonIssues(reason: string): ActionReasonIssue[] {
  if (!reason) {
    return [];
  }

  const markers: { index: number; length: number; issueNumber: string; issueCode: string }[] = [];
  for (const match of reason.matchAll(ISSUE_MARKER)) {
    // matchAll yields `index` relative to the full string, so the segments can
    // be sliced directly below.
    markers.push({
      index: match.index ?? 0,
      length: match[0].length,
      issueNumber: match[1],
      issueCode: match[2],
    });
  }

  if (markers.length === 0) {
    return [];
  }

  const issues: ActionReasonIssue[] = [];

  // Anything before the first marker is not attached to any issue. Keep it so a
  // stray leading sentence is never dropped from the UI.
  const preamble = reason.slice(0, markers[0].index).trim();
  if (preamble) {
    issues.push({ issueNumber: "", issueCode: "", body: preamble });
  }

  markers.forEach((marker, position) => {
    const start = marker.index + marker.length;
    const end =
      position + 1 < markers.length
        ? markers[position + 1].index
        : reason.length;
    const body = reason.slice(start, end).trim();

    // A marker with no prose after it carries no information; skip it rather
    // than rendering an empty bullet.
    if (!body) {
      return;
    }

    issues.push({
      issueNumber: marker.issueNumber,
      issueCode: marker.issueCode,
      body,
    });
  });

  return issues;
}

/** True when the reason should be rendered as a per-issue bullet list. */
export function hasStructuredIssueReasons(reason: string): boolean {
  return parseActionReasonIssues(reason).length > 0;
}