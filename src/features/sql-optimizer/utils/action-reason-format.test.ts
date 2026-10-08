/**
 * action-reason-format.test.ts
 *
 * Regression tests for splitting a planner action's `reason` into per-issue
 * bullets. When the planner merges several issues into one action, the reason
 * is a single run-on paragraph; rendering it whole buries each issue's evidence
 * inside the next one, so the markers are carved out here.
 *
 * The invariants asserted:
 *
 *   1. a multi-issue reason yields one bullet per issue, each keeping its code;
 *   2. no text is ever dropped — a leading sentence is preserved unlabelled;
 *   3. a reason with NO markers returns nothing, so the caller falls back to
 *      rendering the original paragraph rather than a partial list;
 *   4. a marker with no prose after it produces no empty bullet.
 */
import { describe, expect, it } from "vitest";

import {
  hasStructuredIssueReasons,
  parseActionReasonIssues,
} from "./action-reason-format";

/** The real reason shape from a PUSH_DOWN_AGGREGATION action. */
const MERGED_REASON =
  "Issue 1 (ROW_EXPLOSION): The original query performed a LEFT JOIN of the " +
  "10,000-row result from the t1 CROSS JOIN t2 with the InvoiceLine table. " +
  "This join could potentially multiply rows further before aggregation. By " +
  "pre-aggregating InvoiceLine data into a CTE (TrackInvoiceCounts), the " +
  "subsequent LEFT JOIN becomes a join to a much smaller, already aggregated " +
  "set (one row per TrackId). This significantly reduces the number of rows " +
  "processed by the LEFT JOIN and subsequent operations, mitigating the " +
  "ROW_EXPLOSION impact from the InvoiceLine join. Issue 2 " +
  "(TEMP_TABLE_COST): The original query used COUNT(il.InvoiceLineId) as an " +
  "aggregate and then GROUP BY t1.TrackId, t1.Name, t2.TrackId, t2.Name. " +
  "After pre-aggregating InvoiceLine into TrackInvoiceCounts, TotalLines " +
  "becomes COALESCE(tic.InvoiceCount, 0), which is no longer an aggregate in " +
  "the outer query. Removing the redundant GROUP BY eliminates the need for a " +
  "temporary table, which was observed to process 10,000 rows and create 4 " +
  "temporary tables (Created_tmp_tables=4), thus reducing TEMP_TABLE_COST.";

describe("parseActionReasonIssues", () => {
  it("splits the planner's numbered markdown list without leaking markup", () => {
    // This is the shape the planner actually emits: a numbered markdown list
    // with bolded labels. The "1." numbering and the "**" must be consumed as
    // part of the marker, never rendered into the bullet.
    const issues = parseActionReasonIssues(
      "This rewrite addresses multiple issues:\n\n" +
        "1. **Issue 2 (TEMP_TABLE_COST):** The original query did a GROUP BY.\n\n" +
        "2. **Issue 1 (SORT_COST):** The original query sorted 10,000 rows.",
    );

    expect(issues).toHaveLength(3);

    // Leading prose is kept, minus the trailing list marker.
    expect(issues[0].issueCode).toBe("");
    expect(issues[0].body).toBe("This rewrite addresses multiple issues:");

    expect(issues[1].issueCode).toBe("TEMP_TABLE_COST");
    expect(issues[1].issueNumber).toBe("2");
    expect(issues[1].body).toBe("The original query did a GROUP BY.");

    expect(issues[2].issueCode).toBe("SORT_COST");
    expect(issues[2].body).toBe("The original query sorted 10,000 rows.");
  });

  it("leaves no markdown or stray list numbering in any body", () => {
    const issues = parseActionReasonIssues(MARKDOWN_REASON);
    for (const issue of issues) {
      expect(issue.body).not.toContain("**");
      expect(issue.body).not.toMatch(/\b\d+\.\s*$/);
      expect(issue.body.startsWith(" ")).toBe(false);
      expect(issue.body.endsWith(" ")).toBe(false);
    }
  });

  it("splits a merged multi-issue reason into one bullet per issue", () => {
    const issues = parseActionReasonIssues(MERGED_REASON);

    expect(issues).toHaveLength(2);
    expect(issues[0].issueNumber).toBe("1");
    expect(issues[0].issueCode).toBe("ROW_EXPLOSION");
    expect(issues[1].issueNumber).toBe("2");
    expect(issues[1].issueCode).toBe("TEMP_TABLE_COST");
  });

  it("keeps each issue's prose attached to its own marker", () => {
    const [first, second] = parseActionReasonIssues(MERGED_REASON);

    // Evidence must not leak across the boundary between issues.
    expect(first.body).toContain("TrackInvoiceCounts");
    expect(first.body).not.toContain("TEMP_TABLE_COST");
    expect(second.body).toContain("Created_tmp_tables=4");
    expect(second.body).not.toContain("ROW_EXPLOSION");
  });

  it("drops the marker text from the body but keeps the words", () => {
    const [first] = parseActionReasonIssues(MERGED_REASON);
    // The label is rendered separately, so it is stripped from the body.
    expect(first.body.startsWith("Issue 1")).toBe(false);
    // …but the code is still mentioned in the prose, and that must survive.
    expect(first.body).toContain("ROW_EXPLOSION");
  });

  it("preserves text written before the first marker", () => {
    const issues = parseActionReasonIssues(
      "Overall this helps. Issue 1 (SORT_COST): the sort is expensive.",
    );
    expect(issues).toHaveLength(2);
    expect(issues[0].issueCode).toBe("");
    expect(issues[0].body).toBe("Overall this helps.");
    expect(issues[1].issueCode).toBe("SORT_COST");
  });

  it("returns nothing for a reason with no issue markers", () => {
    // The caller must keep rendering these as a plain paragraph.
    expect(
      parseActionReasonIssues(
        "orders is accessed via full table scan touching 149,842 rows.",
      ),
    ).toEqual([]);
  });

  it("returns nothing for empty or blank input", () => {
    expect(parseActionReasonIssues("")).toEqual([]);
    expect(parseActionReasonIssues("   ")).toEqual([]);
  });

  it("handles a single issue marker", () => {
    const issues = parseActionReasonIssues(
      "Issue 1 (INDEX_MISSING): no index covers status.",
    );
    expect(issues).toHaveLength(1);
    expect(issues[0].issueCode).toBe("INDEX_MISSING");
    expect(issues[0].body).toBe("no index covers status.");
  });

  it("skips a marker that has no prose after it", () => {
    // No empty bullet should be rendered for a dangling marker.
    const issues = parseActionReasonIssues(
      "Issue 1 (A): real content. Issue 2 (B):",
    );
    expect(issues).toHaveLength(1);
    expect(issues[0].issueCode).toBe("A");
  });

  it("handles markers with irregular spacing", () => {
    const issues = parseActionReasonIssues(
      "Issue  7 (ROW_EXPLOSION):a. issue 8 (SORT_COST) :b.",
    );
    expect(issues.map((i) => i.issueCode)).toEqual([
      "ROW_EXPLOSION",
      "SORT_COST",
    ]);
    expect(issues[1].body).toBe("b.");
  });

  it("supports issue numbers above nine", () => {
    const issues = parseActionReasonIssues(
      "Issue 10 (SORT_COST): ten. Issue 11 (ROW_EXPLOSION): eleven.",
    );
    expect(issues.map((i) => i.issueNumber)).toEqual(["10", "11"]);
  });

  it("preserves newlines inside an issue body", () => {
    const issues = parseActionReasonIssues(
      "Issue 1 (A): line one.\nline two.\nIssue 2 (B): other.",
    );
    expect(issues[0].body).toContain("line two.");
    expect(issues[1].body).toBe("other.");
  });
});

describe("hasStructuredIssueReasons", () => {
  it("is true only when a reason can be split into issues", () => {
    expect(hasStructuredIssueReasons(MERGED_REASON)).toBe(true);
    expect(hasStructuredIssueReasons("Just a plain reason.")).toBe(false);
    expect(hasStructuredIssueReasons("")).toBe(false);
  });
});