/**
 * OptimizerHeader.test.tsx
 *
 * The header is the only place the daily quota is shown, and the API reports
 * whatever RATE_LIMIT_MAX the server booted with — deliberately raised for
 * local/test runs. These tests pin the display rule that keeps that dev
 * artifact out of the UI while still honouring a genuinely stricter quota.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ThemeProvider } from "../../../../context/ThemeContext";
import OptimizerHeader from "./OptimizerHeader";
import type { SqlQueryUsage } from "../../types/sql-optimizer.types";
import { QUERY_LIMIT } from "../../constants/sql-optimizer.constants";

function headerText(usage: SqlQueryUsage | null): string {
  // The header mounts the shared theme toggle, which reads the theme context.
  return renderToStaticMarkup(
    <ThemeProvider>
      <OptimizerHeader usage={usage} />
    </ThemeProvider>,
  );
}

describe("OptimizerHeader — daily quota line", () => {
  it("reports the product limit when the server is raised for testing", () => {
    // The reported bug: a backend booted with RATE_LIMIT_MAX=100000 made the
    // header read "99996 of 100000 remaining today".
    const text = headerText({
      queriesUsed: 4,
      queriesRemaining: 99996,
      limit: 100000,
      resetsIn: 86400,
    });
    expect(text).toContain(`${QUERY_LIMIT} of ${QUERY_LIMIT} remaining today`);
    expect(text).not.toContain("100000");
    expect(text).not.toContain("99996");
  });

  it("counts down within the product limit", () => {
    const text = headerText({
      queriesUsed: 2,
      queriesRemaining: 3,
      limit: QUERY_LIMIT,
    });
    expect(text).toContain(`3 of ${QUERY_LIMIT} remaining today`);
  });

  it("honours a server limit stricter than the product allowance", () => {
    // A reduced or trial quota must report truthfully, not be rounded up to 5.
    const text = headerText({
      queriesUsed: 1,
      queriesRemaining: 2,
      limit: 3,
    });
    expect(text).toContain(`2 of 3 remaining today`);
    expect(text).not.toContain(`${QUERY_LIMIT} of`);
  });

  it("never reports more remaining than the limit on display", () => {
    const text = headerText({
      queriesUsed: 0,
      queriesRemaining: 99,
      limit: 100000,
    });
    expect(text).toContain(`${QUERY_LIMIT} of ${QUERY_LIMIT} remaining today`);
  });

  it("shows zero rather than a negative count", () => {
    const text = headerText({
      queriesUsed: 7,
      queriesRemaining: -2,
      limit: QUERY_LIMIT,
    });
    expect(text).toContain(`0 of ${QUERY_LIMIT} remaining today`);
  });

  it("falls back to the product limit when the API omits one", () => {
    const text = headerText({ queriesUsed: 1, queriesRemaining: 4 });
    expect(text).toContain(`4 of ${QUERY_LIMIT} remaining today`);
  });

  it("describes the allowance before any usage has loaded", () => {
    const text = headerText(null);
    expect(text).toContain(`Up to ${QUERY_LIMIT} queries per day`);
  });

  it("appends the reset hint when the API reports one", () => {
    const text = headerText({
      queriesUsed: 0,
      queriesRemaining: QUERY_LIMIT,
      limit: QUERY_LIMIT,
      resetsIn: 7200,
    });
    expect(text).toContain("resets in 2h");
  });
});