"""
Plain-English summarizer for the EXPLAIN ANALYZE plan of the REWRITTEN query.

Runs as the last stage of POST /db_optimize_full, strictly after the rewriter
has produced the optimized SQL and that SQL has been re-planned — there is
nothing to summarize until both exist.

This stage is deliberately NARROWER than issue_finder / planner / rewriter: it
is a reader, not an analyst. It describes what one plan already does and
nothing more. It never proposes a fix, never emits SQL, and never compares
against the original plan, because those jobs already belong to the other
three stages and a second opinion on them would contradict the Summary and
Analysis sections shown next to it.

Input : the optimized SQL and its EXPLAIN ANALYZE output.
Output: {"summary": str, "key_points": [str, ...]} — or {} on any failure.
"""

from llm.llm import call_llm
from utils.utils import safe_json_call, safe_print


# The plan tree for a large CROSS JOIN can run long. The summary only needs
# the top of the tree — the operators and timings that dominate — so the
# input is capped rather than sending tens of thousands of plan lines.
_MAX_PLAN_CHARS = 12_000


def format_explain_plan_for_prompt(explain) -> str:
    """
    Flatten a get_explain_analyze() payload into readable plan text.

    PostgreSQL returns a nested JSON tree plus a pre-flattened `summary` dict
    of the costly top-level figures; that dict is far better prompt material
    than the raw tree, so it is preferred when present. MySQL returns the
    pre-indented plan tree under rows[].EXPLAIN, which is used verbatim.
    """
    if not isinstance(explain, dict):
        return ""

    # PostgreSQL: prefer the flattened stats when the helper produced them.
    pg_summary = explain.get("summary")
    if isinstance(pg_summary, dict) and pg_summary:
        parts = []
        for key, value in pg_summary.items():
            if value is None:
                continue
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            parts.append(f"{key}: {value}")
        if parts:
            return "\n".join(parts)

    # MySQL (and the PostgreSQL fallback row shape): join the plan lines.
    rows = explain.get("rows")
    if not isinstance(rows, list):
        return ""

    lines = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        plan = row.get("EXPLAIN") or row.get("QUERY PLAN")
        if isinstance(plan, str) and plan.strip():
            lines.append(plan.rstrip())
    return "\n".join(lines)

async def explain_summarizer_llm(optimized_query: str, explain):
    """
    Summarize the EXPLAIN plan of the optimized query in plain English.

    `explain` is the raw get_explain_analyze() payload; it is flattened here so
    callers do not have to care which engine produced it.

    Returns {} when there is nothing to summarize or the model returns
    something unusable — callers treat that as "no summary", never as failure.
    """
    plan_text = format_explain_plan_for_prompt(explain)
    if not plan_text.strip():
        return {}

    truncated = plan_text[:_MAX_PLAN_CHARS]
    plan_note = (
        ""
        if len(truncated) == len(plan_text)
        else (
            f"\n\nNOTE: The plan was {len(plan_text)} characters and has been "
            f"truncated to the first {len(truncated)}. Read only what is shown."
        )
    )

    system_prompt = """
        You are a world-class MySQL 8.0 and PostgreSQL query execution plan
        analyst. You read EXPLAIN ANALYZE output for a living and can explain
        what a plan is doing in language a developer understands without
        needing to know the engine internals.

        Your job is to explain ONE execution plan in plain English. You are a
        reader, not an analyst and not an optimizer.

        ━━━ WHAT TO WRITE ━━━

        summary
        Two to four sentences describing what this plan actually does, in
        execution order: how the rows are produced, joined, aggregated and
        sorted, and which operators dominate the runtime.

        key_points
        Three to five short bullets. Each one must name a concrete operator,
        table, or timing that appears in the plan you were given.

        ━━━ HOW TO WRITE IT ━━━

        Talk about THIS plan. Name the real operators in it — Index lookup,
        Aggregate, Materialize, Sort, Hash Join, Nested loop, and so on — and
        the real numbers attached to them (rows, loops, actual time).

        Translate engine jargon into outcome. "Aggregate using temporary table
        (actual time=47.5..50.6 rows=10000)" means ten thousand rows are
        written to a temporary table to be grouped, which costs real time.

        Where the plan shows a node feeding another, say how the data flows
        between them so the reader can picture the execution.

        ━━━ HARD CONSTRAINTS ━━━

        Never output SQL.
        Never recommend a fix, an index, a rewrite, or any action.
        Never compare this plan to any other query or any earlier version.
        Never suggest what the user should do about what you describe.
        Never invent a number, table, index, or operator that is not present in
        the input. If the plan does not say it, you do not say it.
        Never output invalid JSON.

        The summary describes the plan you were given. It is not a verdict on
        whether that plan is good or bad.
    """

    prompt = f"""
        Explain the execution plan below in plain English.

        Optimized query:
        {optimized_query}

        EXPLAIN plan of that query:
        {truncated}{plan_note}
    """

    safe_print("This is the prompt currently used in explain summarizer", prompt)

    result = await safe_json_call(call_llm, prompt, system_prompt)

    # Normalise to the advertised shape so the UI never has to guess.
    summary = result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return {}

    raw_points = result.get("key_points")
    key_points = []
    if isinstance(raw_points, list):
        for point in raw_points:
            if isinstance(point, str) and point.strip():
                key_points.append(point.strip())

    return {"summary": summary.strip(), "key_points": key_points}
