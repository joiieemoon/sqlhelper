import { describe, it } from "vitest";
import { parseActionReasonIssues } from "./src/features/sql-optimizer/utils/action-reason-format";
const REAL = "This rewrite addresses multiple issues:\n\n1. **Issue 2 (TEMP_TABLE_COST):** The original query performed a GROUP BY on 10,000 rows.\n\n2. **Issue 1 (SORT_COST):** The original query sorted 10,000 aggregated rows.\n\n3. **Issue 3 (ROW_EXPLOSION):** The CROSS JOIN produces 10,000 rows.";
describe("probe", () => {
  it("shows real-format bodies", () => {
    for (const i of parseActionReasonIssues(REAL)) {
      console.log(`[${i.issueNumber}|${i.issueCode}] body=${JSON.stringify(i.body.slice(0, 55))}`);
    }
  });
});
