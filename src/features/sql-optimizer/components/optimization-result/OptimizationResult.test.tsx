import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { OptimizeQueryResponse } from "../../../../api/types";
import OptimizationResult from "./OptimizationResult";

/**
 * OptimizationResult.test.tsx
 *
 * Regression tests for the three result sections.
 *
 * The optimizer response carries three different kinds of content, and they must
 * stay in separate cards: a narrative summary, a per-action explanation of WHY
 * each rewrite happened, and the run report. These tests assert the split and
 * that the API's structured action data actually reaches the DOM.
 */

const BASE_RESULT: OptimizeQueryResponse = {
  optimized_query: "SELECT 1;",
  actions: [],
  index_recommendations: [],
  summary: [],
  error: null,
  queries_used: null,
  queries_remaining: null,
};

const LIVE_RESULT: OptimizeQueryResponse = {
  ...BASE_RESULT,
  summary: ["Pre-aggregated the COUNT per TrackId to avoid row explosion."],
  actions: ["PUSH_DOWN_AGGREGATION: avoided a row explosion"],
  actionDetails: [
    {
      id: 1,
      type: "PUSH_DOWN_AGGREGATION",
      priority: 1,
      reason: "The CROSS JOIN led to a ROW_EXPLOSION (actual_rows=10800).",
    },
    {
      id: 2,
      type: "SUGGEST_COVERING_INDEX",
      priority: 3,
      table: "Track",
      columns: ["TrackId", "Name"],
      ddl: "CREATE INDEX idx_track_id_name ON Track (TrackId, Name);",
      reason: "Allows an index-only scan.",
    },
  ],
  index_recommendations: [
    "CREATE INDEX idx_track_id_name ON Track (TrackId, Name);",
  ],
  engine: "mysql",
  database: "Chinook",
  elapsed_ms: 98041,
  query_changed: true,
  explain: {
    rows: [
      { EXPLAIN: "-> Sort: TotalLines DESC\n    -> Table scan on <temporary>" },
    ],
    columns: ["EXPLAIN"],
  },
  applied_actions: [1, 2],
  no_op_actions: [2],
};

const render = (result: OptimizeQueryResponse): string =>
  renderToStaticMarkup(<OptimizationResult result={result} />);

describe("OptimizationResult — section split", () => {
  const html = render(LIVE_RESULT);

  it("renders Summary, Analysis & Explanation and Report as separate cards", () => {
    expect(html).toContain("Summary");
    expect(html).toContain("Analysis &amp; Explanation");
    expect(html).toContain("Report");
  });

  it("shows the summary narrative in the Summary card", () => {
    expect(html).toContain("Pre-aggregated the COUNT per TrackId");
  });

  it("keeps index recommendations in the Summary card", () => {
    expect(html).toContain("Index Recommendations");
  });
});

describe("OptimizationResult — Analysis & Explanation", () => {
  const html = render(LIVE_RESULT);

  it("renders the engine's own explanation with the EXPLAIN evidence", () => {
    expect(html).toContain("ROW_EXPLOSION");
    expect(html).toContain("actual_rows=10800");
  });

  it("humanizes the action type", () => {
    expect(html).toContain("Push Down Aggregation");
    expect(html).toContain("Suggest Covering Index");
  });

  it("shows the priority and affected table/columns of each action", () => {
    expect(html).toContain("Priority 1");
    expect(html).toContain("Priority 3");
    expect(html).toContain("Track");
    expect(html).toContain("TrackId, Name");
  });

  it("renders the suggested index DDL", () => {
    expect(html).toContain("CREATE INDEX idx_track_id_name ON Track");
  });

  it("falls back to the flat action strings when no structured data exists", () => {
    const fallbackHtml = render({
      ...BASE_RESULT,
      actions: ["PUSH_DOWN_AGGREGATION: avoided a row explosion"],
    });
    expect(fallbackHtml).toContain("Analysis &amp; Explanation");
    expect(fallbackHtml).toContain("avoided a row explosion");
  });

  it("omits the section entirely when there is nothing to explain", () => {
    const emptyHtml = render(BASE_RESULT);
    expect(emptyHtml).not.toContain("Analysis &amp; Explanation");
  });
});

describe("OptimizationResult — Report", () => {
  const html = render(LIVE_RESULT);

  it("shows the run metadata", () => {
    expect(html).toContain("Engine");
    expect(html).toContain("mysql");
    expect(html).toContain("Database");
    expect(html).toContain("Chinook");
    expect(html).toContain("Query changed");
  });

  it("omits the run duration", () => {
    // Duration was dropped from the Report on purpose — a multi-minute
    // pipeline time is noise next to what actually changed.
    expect(html).not.toContain("Duration");
    expect(html).not.toContain("1m 38s");
  });

  it("shows the action outcome counts", () => {
    expect(html).toContain("Applied: 2");
    expect(html).toContain("No-op: 1");
  });

  it("renders the EXPLAIN plan preserving its indentation", () => {
    expect(html).toContain("EXPLAIN plan (original query)");
    expect(html).toContain("-&gt; Sort: TotalLines DESC\n    -&gt; Table scan");
  });

  it("omits the after-plan block entirely when no optimized plan exists", () => {
    // LIVE_RESULT carries only the original plan, so there is no after-plan
    // heading and no summary caption to show at all.
    expect(html).not.toContain("EXPLAIN plan (optimized query)");
    expect(html).not.toContain(
      "Plan the database runs for the optimized query above.",
    );
  });

  describe("with an optimized-plan summary", () => {
    const summarized = render({
      ...LIVE_RESULT,
      explain_optimized: {
        rows: [{ EXPLAIN: "-> Aggregate  (actual time=0.9..1.2 rows=100)" }],
        columns: ["EXPLAIN"],
      },
      explain_optimized_summary: {
        summary: "Aggregates 100 tracks before the cross join.",
        key_points: ["Materialize eliminated", "Sort over 100 rows"],
      },
    });

    it("shows the after-plan heading", () => {
      expect(summarized).toContain("EXPLAIN plan (optimized query)");
    });

    it("shows the plain-English summary and its key points", () => {
      expect(summarized).toContain("Aggregates 100 tracks before the cross");
      expect(summarized).toContain("Materialize eliminated");
      expect(summarized).toContain("Sort over 100 rows");
    });

    it("drops the placeholder caption once a summary exists", () => {
      expect(summarized).not.toContain(
        "Plan the database runs for the optimized query above.",
      );
    });
  });

  it("keeps the placeholder caption when the plan has no summary", () => {
    // Best-effort stage: a plan without a summary still renders the plan, and
    // keeps the original caption instead of leaving a blank card.
    const planOnly = render({
      ...LIVE_RESULT,
      explain_optimized: {
        rows: [{ EXPLAIN: "-> Aggregate  (actual time=0.9..1.2 rows=100)" }],
        columns: ["EXPLAIN"],
      },
    });
    expect(planOnly).toContain("EXPLAIN plan (optimized query)");
    expect(planOnly).toContain(
      "Plan the database runs for the optimized query above.",
    );
  });

  it("explains itself when the API returned no report data", () => {
    const emptyHtml = render(BASE_RESULT);
    expect(emptyHtml).toContain("No report data was returned");
  });
});

describe("OptimizationResult — no regressions", () => {
  it("still renders the optimized query", () => {
    expect(render(BASE_RESULT)).toContain("SELECT 1;");
  });

  it("shows the no-changes message for an empty summary", () => {
    expect(render(BASE_RESULT)).toContain(
      "No optimization changes were identified",
    );
  });
});