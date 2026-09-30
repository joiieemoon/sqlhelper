/**
 * database-config-validation.test.ts
 *
 * Regression tests for the database-config gate and for the per-field display
 * rule. These rules decide whether Test Connection / Optimize Query may run and
 * which fields get a red ring, so the invariants asserted are:
 *
 *   1. a PARTIALLY filled config is never valid — a live connection must be
 *      complete, otherwise the action buttons stay disabled;
 *   2. an EMPTY config is incomplete too, but shows no per-field errors;
 *   3. `getVisibleDatabaseConfigErrors` reports a field ONLY once that field has
 *      been touched — editing one field must never flag the whole form.
 */
import { describe, expect, it } from "vitest";

import type { DatabaseConfig } from "../types/sql-optimizer.types";
import {
  DATABASE_CONFIG_FIELD_ORDER,
  getFirstDatabaseConfigError,
  getVisibleDatabaseConfigErrors,
  isDatabaseConfigComplete,
  validateDatabaseConfigFields,
} from "./database-config-validation";

const EMPTY_CONFIG: DatabaseConfig = {
  host: "",
  port: "",
  user: "",
  password: "",
  database: "",
  ssl_verify: false,
};

const VALID_CONFIG: DatabaseConfig = {
  ...EMPTY_CONFIG,
  host: "db.example.com",
  port: "3306",
  user: "root",
  password: "s3cret",
  database: "my_database",
};

/** VALID_CONFIG with only the given fields replaced. */
const validWith = (overrides: Partial<DatabaseConfig>): DatabaseConfig => ({
  ...VALID_CONFIG,
  ...overrides,
});

describe("validateDatabaseConfigFields — full config", () => {
  it("accepts a complete config", () => {
    expect(validateDatabaseConfigFields(VALID_CONFIG)).toEqual({});
  });

  it("reports every required field on an empty config", () => {
    expect(Object.keys(validateDatabaseConfigFields(EMPTY_CONFIG)).sort()).toEqual(
      ["database", "host", "password", "port", "user"],
    );
  });

  it("never flags the ssl_verify toggle", () => {
    expect(
      "ssl_verify" in validateDatabaseConfigFields(VALID_CONFIG),
    ).toBe(false);
    expect(
      "ssl_verify" in
        validateDatabaseConfigFields({
          ...VALID_CONFIG,
          ssl_verify: true,
        }),
    ).toBe(false);
  });

  it.each([
    ["", /enter a database host/i],
    ["   ", /enter a database host/i],
    ["bad host", /spaces/i],
    ["h".repeat(256), /too long/i],
  ])("flags host %s", (host, expected) => {
    expect(validateDatabaseConfigFields(validWith({ host })).host).toMatch(
      expected,
    );
  });

  it("accepts a trimmed host", () => {
    expect(
      validateDatabaseConfigFields(validWith({ host: "  localhost  " })).host,
    ).toBeUndefined();
  });

  it.each([
    ["", /enter a database port/i],
    ["3306x", /must be a number/i],
    ["0", /between 1 and 65535/i],
    ["70000", /between 1 and 65535/i],
  ])("flags port %s", (port, expected) => {
    expect(validateDatabaseConfigFields(validWith({ port })).port).toMatch(
      expected,
    );
  });

  it("accepts a valid port", () => {
    expect(
      validateDatabaseConfigFields(validWith({ port: "65535" })).port,
    ).toBeUndefined();
  });

  it.each([
    ["", /enter a database user/i],
    ["u".repeat(129), /too long/i],
  ])("flags user %s", (user, expected) => {
    expect(validateDatabaseConfigFields(validWith({ user })).user).toMatch(
      expected,
    );
  });

  it.each([
    ["", /enter the database password/i],
    ["   ", /enter the database password/i],
    ["p".repeat(513), /too long/i],
  ])("flags password %s", (password, expected) => {
    expect(
      validateDatabaseConfigFields(validWith({ password })).password,
    ).toMatch(expected);
  });

  it.each([
    ["", /enter a database name/i],
    ["my database", /invalid characters/i],
  ])("flags database %s", (database, expected) => {
    expect(
      validateDatabaseConfigFields(validWith({ database })).database,
    ).toMatch(expected);
  });

  it("accepts a database name with _ - $ and digits", () => {
    expect(
      validateDatabaseConfigFields(validWith({ database: "my-db_2$" })).database,
    ).toBeUndefined();
  });

  it("reports only the field that is actually wrong", () => {
    expect(Object.keys(validateDatabaseConfigFields(validWith({ port: "abc" })))).toEqual(
      ["port"],
    );
  });
});

describe("getVisibleDatabaseConfigErrors — per-field display", () => {
  it("shows nothing when no field has been touched", () => {
    expect(
      getVisibleDatabaseConfigErrors(validateDatabaseConfigFields(EMPTY_CONFIG), {}),
    ).toEqual({});
  });

  it("shows ONLY the touched field's error, never the whole form", () => {
    // The reported bug: typing a valid host flagged port/user/password/database
    // too, so the whole form went red on the first keystroke.
    const errors = validateDatabaseConfigFields({
      ...EMPTY_CONFIG,
      host: "localhost",
    });
    // Host itself is valid, and nothing else was touched → nothing is shown.
    expect(getVisibleDatabaseConfigErrors(errors, { host: true })).toEqual({});
    // The other four fields DO have errors, they are just not revealed.
    expect(Object.keys(errors).sort()).toEqual([
      "database",
      "password",
      "port",
      "user",
    ]);
  });

  it("reveals one field at a time as each is visited", () => {
    const errors = validateDatabaseConfigFields({
      ...EMPTY_CONFIG,
      host: "localhost",
    });

    expect(Object.keys(getVisibleDatabaseConfigErrors(errors, {}))).toEqual([]);
    expect(Object.keys(getVisibleDatabaseConfigErrors(errors, { port: true }))).toEqual(
      ["port"],
    );
    expect(
      Object.keys(
        getVisibleDatabaseConfigErrors(errors, { port: true, user: true }),
      ),
    ).toEqual(["port", "user"]);
  });

  it("reports a touched but invalid field on its own", () => {
    const errors = validateDatabaseConfigFields(validWith({ port: "abc" }));
    expect(
      getVisibleDatabaseConfigErrors(errors, { port: true }).port,
    ).toMatch(/must be a number/i);
  });

  it("stays silent on a touched field that is actually valid", () => {
    const errors = validateDatabaseConfigFields(
      validWith({ host: "localhost", port: "" }),
    );
    expect(getVisibleDatabaseConfigErrors(errors, { host: true })).toEqual({});
  });

  it("accumulates errors across several touched fields", () => {
    const errors = validateDatabaseConfigFields(EMPTY_CONFIG);
    const visible = getVisibleDatabaseConfigErrors(errors, {
      host: true,
      database: true,
    });

    expect(Object.keys(visible)).toEqual(["host", "database"]);
  });

  it("never reports a field the user has not visited", () => {
    const errors = validateDatabaseConfigFields(EMPTY_CONFIG);
    for (const touched of [{}, { host: true }, { port: true }]) {
      const visible = getVisibleDatabaseConfigErrors(errors, touched);
      for (const field of Object.keys(visible)) {
        expect(touched).toHaveProperty(field);
      }
    }
  });

  it("returns keys in DATABASE_CONFIG_FIELD_ORDER", () => {
    const errors = validateDatabaseConfigFields(EMPTY_CONFIG);
    const allTouched = Object.fromEntries(
      DATABASE_CONFIG_FIELD_ORDER.map((field) => [field, true]),
    );
    expect(Object.keys(getVisibleDatabaseConfigErrors(errors, allTouched))).toEqual(
      [...DATABASE_CONFIG_FIELD_ORDER],
    );
  });
});

describe("isDatabaseConfigComplete — the action gate", () => {
  it("is false for an ALL-EMPTY config (buttons stay disabled)", () => {
    expect(isDatabaseConfigComplete(EMPTY_CONFIG)).toBe(false);
  });

  it("is false for a partially filled config", () => {
    expect(
      isDatabaseConfigComplete({ ...EMPTY_CONFIG, host: "localhost" }),
    ).toBe(false);
    expect(isDatabaseConfigComplete(validWith({ password: "" }))).toBe(false);
    expect(isDatabaseConfigComplete(validWith({ port: "abc" }))).toBe(false);
  });

  it("is true once every field holds valid details", () => {
    expect(isDatabaseConfigComplete(VALID_CONFIG)).toBe(true);
    expect(
      isDatabaseConfigComplete({ ...VALID_CONFIG, ssl_verify: true }),
    ).toBe(true);
  });
});

describe("getFirstDatabaseConfigError", () => {
  it("returns null for a valid config", () => {
    expect(
      getFirstDatabaseConfigError(validateDatabaseConfigFields(VALID_CONFIG)),
    ).toBeNull();
  });

  it("returns the host message first on an empty config", () => {
    expect(
      getFirstDatabaseConfigError(validateDatabaseConfigFields(EMPTY_CONFIG)),
    ).toMatch(/enter a database host/i);
  });

  it("follows DATABASE_CONFIG_FIELD_ORDER for multi-field failures", () => {
    const errors = validateDatabaseConfigFields(
      validWith({ host: "bad host", port: "abc", database: "no" }),
    );
    expect(getFirstDatabaseConfigError(errors)).toMatch(/bad host|spaces/i);
    expect(DATABASE_CONFIG_FIELD_ORDER.slice(0, 3)).toEqual([
      "host",
      "port",
      "user",
    ]);
  });
});
