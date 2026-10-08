import { describe, expect, it } from "vitest";
import raw from "./_real.json";
import {
  coerceOptimizerActionList,
  coerceOptimizerDisplayList,
  coerceOptimizerExplain,
  coerceOptimizerExplainSummary,
} from "./src/api/utils/optimizer-response-format";

describe("real payload", () => {
  it("survives the normalizer coercers", () => {
    const data = raw as unknown as Record<string, unknown>;
    let err: string | null = null;
    try {
      const d = coerceOptimizerDisplayList(data.changes);
      const s = coerceOptimizerDisplayList(data.summary);
      const a = coerceOptimizerDisplayList(data.actions);
      const ad = coerceOptimizerActionList(data.actions);
      const ex = coerceOptimizerExplain(data.explain);
      const exo = coerceOptimizerExplain(data.explain_optimized);
      const summ = coerceOptimizerExplainSummary(data.explain_optimized_summary);
      console.log("OK changes=", d.length, "summary=", s.length, "actions=", a.length,
                  "actionDetails=", ad.length, "explain=", !!ex, "explainOptimized=", !!exo,
                  "summaryObj=", !!summ, "points=", summ?.key_points?.length);
    } catch (e) {
      err = String(e);
    }
    console.log("ERROR:", err);
    expect(err).toBeNull();
  });
});
