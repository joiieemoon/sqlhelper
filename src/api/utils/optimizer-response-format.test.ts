import { describe, expect, it } from "vitest";

import {
  coerceOptimizerActionList,
  coerceOptimizerDisplayList,
  coerceOptimizerExplain,
} from "./optimizer-response-format";

/**
 * Regression tests for the optimizer response parsers.
 *
 * The live pipeline returns `actions` as rich objects and the plan under
 * `explain.rows[]`. The display-list parser flattens those objects to
 * "TYPE: reason" strings, which is fine for the compact list but discards the
 * fields the "Analysis & Explanation" and "Report" sections need. These tests
 * pin the structured extraction — using a payload shaped like the real
 * /db_optimize_full response — and the defensive handling of malformed data.
 */

/** One real action object, trimmed to the fields that matter here. */
const REAL_ACTION = {
  id: 1,
  issue_id: [1, 2],
  type: "PUSH_DOWN_AGGREGATION",
  priority: 1,
  before: "select count(il.InvoiceLineId) ...",
  after: "WITH TrackLineCounts AS (...)",
  table: "",
  columns: [],
  join_order: [],
  predicate: "",
  hint: "",
  ddl: "",
  reason:
    "The original query's CROSS JOIN leads to a ROW_EXPLOSION (actual_rows=10800) and TEMP_TABLE_COST.",
  conflicts_with: [],
};

/** Index-suggestion action: no before/after, carries DDL + table + columns. */
const INDEX_ACTION = {
  id: 2,
  issue_id: [3],
  type: "SUGGEST_COVERING_INDEX",
  priority: 3,
  before: "",
  after: "",
  table: "Track",
  columns: ["TrackId", "Name"],
  ddl: "CREATE INDEX idx_track_id_name ON Track (TrackId, Name);",
  reason: "Creating a covering index would allow an index-only scan.",
  conflicts_with: [],
};

describe("coerceOptimizerActionList — structured actions", () => {
  it("keeps the reason that explains the rewrite", () => {
    const [action] = coerceOptimizerActionList([REAL_ACTION]);
    expect(action.reason).toMatch(/ROW_EXPLOSION/);
    expect(action.type).toBe("PUSH_DOWN_AGGREGATION");
    expect(action.priority).toBe(1);
    expect(action.id).toBe(1);
  });

  it("keeps before/after and the DDL of an index suggestion", () => {
    const [indexAction] = coerceOptimizerActionList([INDEX_ACTION]);
    expect(indexAction.ddl).toBe(
      "CREATE INDEX idx_track_id_name ON Track (TrackId, Name);",
    );
    expect(indexAction.table).toBe("Track");
    expect(indexAction.columns).toEqual(["TrackId", "Name"]);
  });

  it("preserves issue_id links between actions", () => {
    const [action] = coerceOptimizerActionList([REAL_ACTION]);
    expect(action.issue_id).toEqual([1, 2]);
  });

  it("returns an empty array for a non-array or missing value", () => {
    expect(coerceOptimizerActionList(undefined)).toEqual([]);
    expect(coerceOptimizerActionList(null)).toEqual([]);
    expect(coerceOptimizerActionList("nope")).toEqual([]);
  });

  it("ignores plain-string actions (already covered by the display list)", () => {
    expect(coerceOptimizerActionList(["just a string"])).toEqual([]);
  });

  it("skips objects with no recognised content", () => {
    expect(coerceOptimizerActionList([{ unknown_field: true }])).toEqual([]);
  });

  it("tolerates wrong types inside an action without throwing", () => {
    // Wrong-typed fields are dropped; a valid sibling field still makes the
    // action worth keeping.
    const [action] = coerceOptimizerActionList([
      { id: "1", type: 5, reason: "kept", columns: "TrackId" },
    ]);
    expect(action.id).toBeUndefined();
    expect(action.type).toBeUndefined();
    expect(action.columns).toBeUndefined();
    expect(action.reason).toBe("kept");
  });

  it("still flattens the same payload for the legacy display list", () => {
    const display = coerceOptimizerDisplayList([REAL_ACTION]);
    expect(display).toHaveLength(1);
    expect(display[0]).toMatch(/^PUSH_DOWN_AGGREGATION: /);
  });
});

describe("coerceOptimizerExplain — original query plan", () => {
  const EXPLAIN_PAYLOAD = {
    rows: [
      {
        EXPLAIN:
          "-> Sort: TotalLines DESC  (actual time=30.9..32.1 rows=10000 loops=1)\n    -> Table scan on <temporary>  (actual time=22.4..24.5 rows=10000 loops=1)",
      },
    ],
    columns: ["EXPLAIN"],
  };

  it("preserves the multi-line plan tree verbatim", () => {
    const explain = coerceOptimizerExplain(EXPLAIN_PAYLOAD);
    expect(explain?.rows).toHaveLength(1);
    // Indentation is part of the plan tree — it must survive untouched.
    expect(explain?.rows?.[0].EXPLAIN).toContain("\n    -> Table scan");
    expect(explain?.columns).toEqual(["EXPLAIN"]);
  });

  it("returns undefined when there is no plan", () => {
    expect(coerceOptimizerExplain(undefined)).toBeUndefined();
    expect(coerceOptimizerExplain({ rows: [] })).toBeUndefined();
    expect(coerceOptimizerExplain({})).toBeUndefined();
    expect(coerceOptimizerExplain("nope")).toBeUndefined();
  });

  it("drops rows with an empty EXPLAIN string", () => {
    expect(coerceOptimizerExplain({ rows: [{ EXPLAIN: "   " }] })).toBeUndefined();
  });
});