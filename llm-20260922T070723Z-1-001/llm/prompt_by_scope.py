JOIN_SCOPE_PROMPT = """

Analyze ONLY join-related performance issues.

Reason independently about:
- join order efficiency
- row explosion across joins
- late filtering after joins
- excessive nested loop behavior
- high loop counts
- unnecessary joins
- join cardinality amplification
- poor driving table selection
- join execution inefficiencies
- excessive intermediate result growth
- joins causing temp tables or filesorts
- inefficient join paths visible in execution data

Use the execution plan, runtime row counts, loops, filters, and schema relationships as evidence.

Do not discuss indexes unless directly impacting join execution.
Do not discuss generic SQL style issues.
Only report real join-related performance problems supported by evidence.
"""


INDEX_SCOPE_PROMPT = """

Analyze ONLY index-related performance issues.

Reason independently about:
- full table scans
- inefficient index usage
- missing index opportunities
- poor index selectivity
- post-fetch filtering
- filesort caused by missing index coverage
- temp tables caused by missing grouping coverage
- inefficient filtering access paths
- non-covering index access
- excessive rows examined
- optimizer choosing weak indexes
- scans that should be index lookups
- filter predicates not pushed into indexes

Use execution statistics, access types, rows touched, filters, and schema index metadata as evidence.

Do not discuss join strategy unless directly related to index usage.
Do not discuss generic SQL structure issues.
Only report real index-related performance problems supported by evidence.
"""

KEY_SCOPE_PROMPT = """

Analyze ONLY key and key-design related performance issues.

Reason independently about:
- inefficient primary key access patterns
- foreign key access inefficiencies
- missing supporting foreign key indexes
- weak composite key design
- excessive repeated key lookups
- key usage creating high loop amplification
- nullable key behavior impacting execution
- poor multi-column key structure
- inefficient key access paths
- mismatched join key behavior
- redundant key access work

Use schema metadata, execution loops, row access patterns, and execution statistics as evidence.

Do not discuss general indexing strategy outside key behavior.
Do not discuss SQL structure problems.
Only report real key-related performance problems supported by evidence.
"""

SQL_OPT_SCOPE_PROMPT = """

Analyze ONLY SQL structure and query optimization issues.

Reason independently about:
- unnecessary computation
- excessive aggregation work
- inefficient GROUP BY behavior
- unnecessary DISTINCT usage
- redundant sorting
- large unbounded ORDER BY operations
- filters applied too late
- inefficient query structure
- repeated computation
- unnecessary data retrieval
- expensive expressions
- poor aggregation flow
- avoidable intermediate result growth
- query patterns causing unnecessary work
- inefficient pagination
- functions preventing efficient execution
- structural SQL anti-patterns

Use execution behavior, row counts, grouping behavior, sorting behavior, and query structure as evidence.

Do not discuss pure indexing problems unless directly caused by SQL structure.
Do not discuss key-design problems.
Only report real SQL optimization problems supported by evidence.
"""


# CUSTOM_REQUEST_PROMPT = f"""

# Analyze the query against the following user requirements and constraints:

# {custom_request}

# Reason independently about whether the query violates, ignores, or poorly satisfies any requirement.

# Treat all user requirements as hard constraints.

# Only report issues directly related to the custom request and supported by evidence from the query or execution data.
# """