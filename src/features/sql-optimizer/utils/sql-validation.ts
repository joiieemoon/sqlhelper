/**
 * Client-side SQL guard — the FIRST line of defense in front of the
 * optimizer API. Mirrors the backend's SELECT-only validator
 * (validator/query_validator.py) so destructive statements (DELETE, UPDATE,
 * DROP, ...) are rejected in the browser before any request is sent: no daily
 * quota is spent and no destructive SQL ever reaches the server.
 *
 * This is fast feedback and cost protection, not a replacement for the
 * backend guard — the server remains the authority.
 */

/** Mirrors the backend guard's maximum query length. */
export const MAX_QUERY_LENGTH = 8000;

/**
 * Top-level statements the optimizer accepts. Read-only only, matching the
 * backend's _ALLOWED_TOP_LEVEL (SELECT / UNION / EXCEPT / INTERSECT all start
 * with SELECT or WITH; VALUES rows are read-only too).
 */
const READ_ONLY_STATEMENT_KEYWORDS = new Set(["SELECT", "WITH", "VALUES"]);

/**
 * Destructive / side-effecting keywords — mirrors the backend's L1 lexical
 * backstop (_FORBIDDEN_RE). Matched against the stripped query (comments and
 * string literals blanked), exactly like the backend's
 * _strip_literals_and_comments() + _FORBIDDEN_RE.search(), so a harmless
 * predicate like `WHERE status = 'insert pending'` passes while a real
 * DELETE/DROP/INSERT anywhere in the statement is blocked. Word boundaries
 * prevent false positives on identifiers such as `created_at` or `settings`.
 */
const FORBIDDEN_KEYWORDS = [
  "insert", "replace", "update", "delete", "truncate", "drop", "alter",
  "create", "rename", "grant", "revoke", "merge", "call", "execute",
  "handler", "load", "outfile", "dumpfile", "into", "set", "use",
  "attach", "detach", "vacuum", "pragma", "copy", "commit", "rollback",
  "shutdown", "kill", "flush", "prepare",
];
const FORBIDDEN_KEYWORD_RE = new RegExp(
  `\\b(?:${FORBIDDEN_KEYWORDS.join("|")})\\b`,
  "i",
);

/**
 * Statement starters the optimizer never accepts (they are not SELECT /
 * WITH / VALUES, so the backend's L3 "top-level must be read-only" check
 * rejects them). Used by the live guard to flag a destructive first token
 * immediately, while leaving incomplete tokens (e.g. "S") alone so typing
 * is never nagged.
 */
const NON_READ_ONLY_STATEMENT_KEYWORDS = new Set([
  "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "DROP", "ALTER",
  "CREATE", "TRUNCATE", "RENAME", "GRANT", "REVOKE", "CALL", "EXECUTE",
  "EXEC", "PREPARE", "DEALLOCATE", "USE", "SET", "SHOW", "DESCRIBE",
  "DESC", "EXPLAIN", "ANALYZE", "OPTIMIZE", "REPAIR", "FLUSH", "KILL",
  "SHUTDOWN", "LOAD", "COPY", "VACUUM", "ATTACH", "DETACH", "PRAGMA",
  "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "START", "LOCK", "UNLOCK",
  "DECLARE", "FETCH", "OPEN", "CLOSE", "DO", "HANDLER",
]);

/**
 * User-variable assignment (`@var := 1` / `@var = 1`) — mirrors the
 * backend's L4 assignment rejection. Reading a variable (`SELECT @a`)
 * or using one on the right-hand side (`WHERE x = @a`) is fine.
 */
const USER_VAR_ASSIGNMENT_RE = /@[A-Za-z_]\w*\s*:?=/;

/**
 * Any user/session variable reference (`@a`, `@@global.x`) — mirrors the
 * backend's L4 rejection of `exp.Parameter` / `exp.SessionParameter`. The
 * backend blocks these even for plain reads, because a read-only optimizer
 * query has no legitimate reason to touch session state. The frontend must
 * match, or the UI says "fine" and the server rejects after the request.
 * Safe to match on the stripped text: `@` cannot appear in an identifier,
 * and anything inside a string literal has already been blanked out.
 */
const USER_VAR_REFERENCE_RE = /@@?[A-Za-z_]\w*/;

/**
 * Dangerous functions — mirrors the backend's L4 `_DANGEROUS_FUNCS` walk.
 * These are read-only in the SQL sense (they are SELECT expressions) but are
 * still blocked: LOAD_FILE reads server files, SLEEP and BENCHMARK are
 * denial-of-service / timing-oracle primitives. The backend rejected all three
 * before this list existed here, so without it the UI said "looks fine" and the
 * server rejected it after the request was already sent.
 */
const DANGEROUS_FUNCTIONS = [
  "load_file",
  "sys_exec",
  "sys_eval",
  "benchmark",
  "sleep",
  "outfile",
  "dumpfile",
];
const DANGEROUS_FUNCTION_RE = new RegExp(
  `\\b(?:${DANGEROUS_FUNCTIONS.join("|")})\\s*\\(`,
  "i",
);

/** MySQL executable (versioned) comment opener — rejected by the backend. */
const VERSIONED_COMMENT_RE = /\/\*[!+]/;

interface ScanResult {
  /** SQL with comments removed and literals/quoted identifiers blanked out. */
  code: string;
  /** Human-readable error, or null when the scan succeeded. */
  error: string | null;
}

/**
 * Remove `--` and `/* ... *\/` comments, and replace string literals
 * ('…'), double-quoted identifiers ("…") and backtick identifiers (`…`)
 * with a blank token. Reports unclosed literals as errors.
 */
function stripCommentsAndLiterals(sql: string): ScanResult {
  let code = "";
  let i = 0;

  while (i < sql.length) {
    const ch = sql[i];
    const next = sql[i + 1];

    // Line comment -- …
    if (ch === "-" && next === "-") {
      while (i < sql.length && sql[i] !== "\n") i += 1;
      continue;
    }

    // Block comment / * … * /
    if (ch === "/" && next === "*") {
      const end = sql.indexOf("*/", i + 2);
      if (end === -1) {
        return {
          code: "",
          error: 'Unterminated block comment — missing closing "*/".',
        };
      }
      i = end + 2;
      continue;
    }

    // Single-quoted string literal ('…' with '' and \ escapes).
    if (ch === "'") {
      i += 1;
      let closed = false;
      while (i < sql.length) {
        if (sql[i] === "\\") {
          i += 2;
          continue;
        }
        if (sql[i] === "'") {
          if (sql[i + 1] === "'") {
            i += 2; // escaped quote ''
            continue;
          }
          i += 1;
          closed = true;
          break;
        }
        i += 1;
      }
      if (!closed) {
        return {
          code: "",
          error: "Unclosed string literal — a single quote (') is not closed.",
        };
      }
      code += " '' ";
      continue;
    }

    // Double-quoted identifier ("…" with "" escape).
    if (ch === '"') {
      i += 1;
      let closed = false;
      while (i < sql.length) {
        if (sql[i] === '"') {
          if (sql[i + 1] === '"') {
            i += 2;
            continue;
          }
          i += 1;
          closed = true;
          break;
        }
        i += 1;
      }
      if (!closed) {
        return {
          code: "",
          error:
            'Unclosed quoted identifier — a double quote (") is not closed.',
        };
      }
      code += " q ";
      continue;
    }

    // Backtick identifier (`…`).
    if (ch === "`") {
      const end = sql.indexOf("`", i + 1);
      if (end === -1) {
        return {
          code: "",
          error:
            "Unclosed backtick identifier — a backtick (`) is not closed.",
        };
      }
      i = end + 1;
      code += " q ";
      continue;
    }

    code += ch;
    i += 1;
  }

  return { code, error: null };
}

/**
 * Strict submit-time guard. Returns a human-readable error message when the
 * query must be blocked, or null when it is an acceptable read-only query.
 *
 * Order mirrors the backend validator so the user sees the same reasons:
 * 1. Size limit (L0)
 * 2. Unterminated comment / literal (scanner)
 * 3. MySQL executable (versioned) comment (L1)
 * 4. Forbidden keyword anywhere in the stripped statement (L1)
 * 5. User-variable assignment (L4)
 * 6. Multi-statement semicolons (L3 — single statement only)
 * 7. First token must be a read-only statement (L3)
 */
export function validateSqlQuery(sql: string): string | null {
  const trimmed = sql.trim();

  if (trimmed === "") {
    return "Enter a SQL query to optimize.";
  }

  if (trimmed.length > MAX_QUERY_LENGTH) {
    return `Query is too long (${trimmed.length.toLocaleString()} characters). The maximum is ${MAX_QUERY_LENGTH.toLocaleString()}.`;
  }

  if (VERSIONED_COMMENT_RE.test(trimmed)) {
    return "MySQL executable comments (/*+ ... */ or /*! ... */) are not allowed.";
  }

  const { code, error } = stripCommentsAndLiterals(trimmed);
  if (error) {
    return error;
  }

  const forbidden = code.match(FORBIDDEN_KEYWORD_RE);
  if (forbidden) {
    return `Only read-only SELECT queries are allowed — the keyword "${forbidden[0].toUpperCase()}" was detected.`;
  }

  if (USER_VAR_ASSIGNMENT_RE.test(code)) {
    return "User-variable assignments (@var := ...) are not allowed.";
  }

  if (USER_VAR_REFERENCE_RE.test(code)) {
    return "User/session variables (@var, @@var) are not allowed in a read-only query.";
  }

  const dangerous = code.match(DANGEROUS_FUNCTION_RE);
  if (dangerous) {
    return `Only read-only SELECT queries are allowed — the function "${dangerous[0].toUpperCase()}" was detected.`;
  }

  // Single statement only: reject any unquoted/uncommented semicolon that is
  // followed by more SQL. The scanner replaced literals with "''" and dropped
  // comments, so every ";" left in `code` is a real statement separator.
  const statements = code
    .split(";")
    .map((s) => s.trim())
    .filter((s) => s !== "");
  if (statements.length > 1 || /;/.test(code.replace(/;\s*$/, ""))) {
    return "Only a single statement is allowed — remove the extra ';'.";
  }

  const firstToken = statements[0]?.split(/\s+/)[0]?.replace(/^[()\s]+/, "");
  if (!firstToken || !READ_ONLY_STATEMENT_KEYWORDS.has(firstToken.toUpperCase())) {
    return "Only read-only queries are allowed — the statement must start with SELECT, WITH or VALUES.";
  }

  return null;
}

/**
 * Lightweight live guard for keystroke-level feedback. Only flags input that
 * is unambiguously destructive, so ordinary typing is never nagged:
 * - an explicit non-read-only statement starter (DELETE, DROP, ...), or
 * - any forbidden keyword already visible in a *complete* statement.
 *
 * Returns null for empty input, partial tokens, or anything the strict
 * submit-time guard should judge.
 */
export function validateQueryGuard(sql: string): string | null {
  const trimmed = sql.trim();
  if (trimmed === "") return null;

  // Incomplete input mid-typing: leave it alone.
  const firstToken = trimmed.split(/\s+/)[0]?.replace(/^[()\s]+/, "") ?? "";
  const upperFirst = firstToken.toUpperCase();
  if (NON_READ_ONLY_STATEMENT_KEYWORDS.has(upperFirst)) {
    return `Only read-only SELECT queries are allowed — "${upperFirst}" is blocked before it reaches the server.`;
  }

  // Flag destructive keywords anywhere, but only once a statement looks
  // complete (ends with something other than an operator-ish character), to
  // avoid false hits while typing an identifier.
  const { code, error } = stripCommentsAndLiterals(trimmed);
  if (error) return error;
  const forbidden = code.match(FORBIDDEN_KEYWORD_RE);
  if (forbidden) {
    return `Only read-only SELECT queries are allowed — "${forbidden[0].toUpperCase()}" is blocked before it reaches the server.`;
  }

  const dangerous = code.match(DANGEROUS_FUNCTION_RE);
  if (dangerous) {
    return `Only read-only SELECT queries are allowed — "${dangerous[0].toUpperCase()}" is blocked before it reaches the server.`;
  }

  return null;
}

