/**
 * Database connection config validation — the FULL config check.
 *
 * Pure, React-free rules shared by three callers:
 * - the config panel (per-field message + red ring, only for touched fields),
 * - Test Connection (refused while the config is invalid),
 * - Optimize Query (live credentials must be complete before a request).
 *
 * `validateDatabaseConfigFields` is the FULL check and always reports every
 * problem it finds. What the user actually SEES is decided separately by
 * `getVisibleDatabaseConfigErrors` — the panel only shows a field's message
 * once that field has been touched, so editing one field never lights up the
 * whole form. A complete config (an all-empty one included) is what the action
 * gate requires; see `isDatabaseConfigComplete`.
 */

import type {
  DatabaseConfig,
  DatabaseConfigErrors,
  DatabaseConfigField,
  DatabaseConfigTouched,
} from "../types/sql-optimizer.types";

/**
 * Field order used whenever a single message has to represent the whole config
 * (toast / panel summary). Keeps the "first error" deterministic.
 */
export const DATABASE_CONFIG_FIELD_ORDER: readonly DatabaseConfigField[] = [
  "host",
  "port",
  "user",
  "password",
  "database",
];

/**
 * Validates every field of the connection config and returns one message per
 * invalid field. Returns an empty object when the whole config is valid.
 * `ssl_verify` is a toggle and can never be invalid.
 */
export function validateDatabaseConfigFields(
  config: DatabaseConfig,
): DatabaseConfigErrors {
  const errors: DatabaseConfigErrors = {};

  const host = config.host.trim();
  if (!host) {
    errors.host = "Enter a database host.";
  } else if (/\s/.test(host)) {
    errors.host = "Database host must not contain spaces.";
  } else if (host.length > 255) {
    errors.host = "Database host is too long (max 255 characters).";
  }

  const port = config.port.trim();
  if (!port) {
    errors.port = "Enter a database port.";
  } else if (!/^\d{1,5}$/.test(port)) {
    errors.port = "Database port must be a number (1–65535).";
  } else {
    const portNumber = Number(port);
    if (portNumber < 1 || portNumber > 65535) {
      errors.port = "Database port must be between 1 and 65535.";
    }
  }

  const user = config.user.trim();
  if (!user) {
    errors.user = "Enter a database user.";
  } else if (user.length > 128) {
    errors.user = "Database user is too long (max 128 characters).";
  }

  if (config.password.trim() === "") {
    errors.password =
      "Enter the database password — it is not saved between sessions.";
  } else if (config.password.length > 512) {
    errors.password = "Database password is too long (max 512 characters).";
  }

  const database = config.database.trim();
  if (!database) {
    errors.database = "Enter a database name.";
  } else if (!/^[A-Za-z0-9_$-]+$/.test(database)) {
    errors.database = "Database name contains invalid characters.";
  }

  return errors;
}

/** First invalid-field message in `DATABASE_CONFIG_FIELD_ORDER`, or null when valid. */
export function getFirstDatabaseConfigError(
  errors: DatabaseConfigErrors,
): string | null {
  for (const field of DATABASE_CONFIG_FIELD_ORDER) {
    const message = errors[field];
    if (message) return message;
  }
  return null;
}

/**
 * Narrows the full error map down to the fields the user has actually touched.
 *
 * This is what keeps the panel quiet: typing into Host validates Host only —
 * Port / User / Password / Database stay silent until they are visited, instead
 * of the whole form turning red on the first keystroke. Errors are returned in
 * `DATABASE_CONFIG_FIELD_ORDER` so the resulting object is deterministic.
 */
export function getVisibleDatabaseConfigErrors(
  errors: DatabaseConfigErrors,
  touched: DatabaseConfigTouched,
): DatabaseConfigErrors {
  const visible: DatabaseConfigErrors = {};
  for (const field of DATABASE_CONFIG_FIELD_ORDER) {
    const message = errors[field];
    if (message && touched[field]) {
      visible[field] = message;
    }
  }
  return visible;
}

/**
 * True when the config holds a complete, valid live connection.
 *
 * The UI requires this before Test Connection or Optimize Query may run — an
 * ALL-EMPTY config is incomplete as well, so the action buttons stay disabled
 * until every field is filled with valid details. (The API itself would treat an
 * empty `database_config` as syntax-only optimization; the UI does not offer
 * that path.)
 */
export function isDatabaseConfigComplete(config: DatabaseConfig): boolean {
  return (
    getFirstDatabaseConfigError(validateDatabaseConfigFields(config)) === null
  );
}
