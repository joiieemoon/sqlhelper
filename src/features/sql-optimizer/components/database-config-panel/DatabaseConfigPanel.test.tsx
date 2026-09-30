/**
 * DatabaseConfigPanel.test.tsx
 *
 * Regression tests for the config panel's validation feedback — the only place
 * the user can see WHY Test Connection / Optimize Query are disabled. Each
 * invalid field must show its message plus the red ring, the actions must be
 * disabled while the config is incomplete, and a clean (or empty) config must
 * come back to a normal, enabled panel.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { DatabaseConfig } from "../../types/sql-optimizer.types";
import DatabaseConfigPanel from "./DatabaseConfigPanel";

const CLEAN_CONFIG: DatabaseConfig = {
  host: "db.example.com",
  port: "3306",
  user: "root",
  password: "s3cret",
  database: "my_database",
  ssl_verify: false,
};

const RING_CLASS = "ring-2 ring-error-500/40";

const noop = () => {};

function renderPanel(overrides: {
  config?: DatabaseConfig;
  configErrors?: Record<string, string>;
  isConfigInvalid?: boolean;
}): string {
  return renderToStaticMarkup(
    <DatabaseConfigPanel
      config={overrides.config ?? CLEAN_CONFIG}
      configErrors={overrides.configErrors ?? {}}
      isConfigInvalid={overrides.isConfigInvalid ?? false}
      onChange={noop}
      onBlurField={noop}
      onTestConnection={noop}
      isTestingConnection={false}
      connectionStatus="idle"
      connectionError={null}
    />,
  );
}

describe("DatabaseConfigPanel — invalid config", () => {
  const html = renderPanel({
    config: { ...CLEAN_CONFIG, user: "", password: "" },
    configErrors: {
      user: "Enter a database user.",
      password: "Enter the database password — it is not saved between sessions.",
    },
    isConfigInvalid: true,
  });

  it("red-rings every invalid field", () => {
    expect(html.split(RING_CLASS).length - 1).toBe(2);
  });

  it("shows the per-field messages", () => {
    expect(html).toContain("Enter a database user.");
    expect(html).toContain("Enter the database password");
  });

  it("explains why the actions are disabled", () => {
    expect(html).toContain("Connection details incomplete");
  });

  it("disables Test Connection", () => {
    expect(html).toMatch(/<button[^>]*disabled[^>]*>\s*Test Connection/);
  });
});

describe("DatabaseConfigPanel — valid config", () => {
  const html = renderPanel({});

  it("renders no rings and no incomplete hint", () => {
    expect(html).not.toContain("ring-error-500");
    expect(html).not.toContain("Connection details incomplete");
  });

  it("leaves Test Connection enabled", () => {
    expect(html).toMatch(/<button[^>]*>\s*Test Connection/);
    expect(html).not.toMatch(/<button[^>]*disabled/);
  });

  it("wires every field value to the config", () => {
    expect(html).toContain('value="db.example.com"');
    expect(html).toContain('value="3306"');
    expect(html).toContain('value="root"');
    expect(html).toContain('value="s3cret"');
    expect(html).toContain('value="my_database"');
  });
});

describe("DatabaseConfigPanel — all fields empty", () => {
  it("shows no rings, explains the disabled state, and disables the actions", () => {
    const html = renderPanel({
      config: {
        host: "",
        port: "",
        user: "",
        password: "",
        database: "",
        ssl_verify: false,
      },
      configErrors: {},
      isConfigInvalid: true,
    });

    // Nothing is highlighted before the user has typed anything...
    expect(html).not.toContain("ring-error-500");
    // ...but the reason both actions are disabled is always visible.
    expect(html).toContain("Connection details incomplete");
    expect(html).toContain("host, port, user, password and database");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>\s*Test Connection/);
  });
});
