/**
 * Database configuration panel.
 * Credentials are sent to POST /db_optimize_test and /db_optimize_full.
 */

import { Alert } from "../../../../components/common/alert";
import Input from "../../../../components/form/input/input-fields";
import Button from "../../../../components/ui/button/Button";
import Checkbox from "../../../../components/form/input/components/checkbox/Checkbox";
import type {
  ConnectionStatus,
  DatabaseConfig,
  DatabaseConfigErrors,
  DatabaseConfigField,
} from "../../types/sql-optimizer.types";
import {
  DATABASE_CONFIG_HINT,
  DATABASE_CONFIG_INCOMPLETE_HINT,
} from "../../constants/sql-optimizer.constants";
import SectionLabel from "../section-label/SectionLabel";

interface DatabaseConfigPanelProps {
  config: DatabaseConfig;
  /** Per-field validation messages — a field with a message gets a red ring. */
  configErrors: DatabaseConfigErrors;
  /** True while live credentials are incomplete (blocks both actions below). */
  isConfigInvalid: boolean;
  onChange: (field: keyof DatabaseConfig, value: string | boolean) => void;
  /** Marks a field as visited on blur so it can show its own error message. */
  onBlurField: (field: keyof DatabaseConfig) => void;
  onTestConnection: () => void;
  isTestingConnection: boolean;
  connectionStatus: ConnectionStatus;
  connectionError: string | null;
}

/** Ring drawn around a field whose validation failed, next to the red border. */
const INVALID_FIELD_RING = "ring-2 ring-error-500/40";

const connectionStatusLabel: Record<ConnectionStatus, string | null> = {
  idle: null,
  testing: "Testing connection…",
  connected: "Connected",
  failed: "Connection failed",
};

const DatabaseConfigPanel: React.FC<DatabaseConfigPanelProps> = ({
  config,
  configErrors,
  isConfigInvalid,
  onChange,
  onBlurField,
  onTestConnection,
  isTestingConnection,
  connectionStatus,
  connectionError,
}) => {
  /** Ring class for a field whose validation failed. */
  const errorRing = (field: DatabaseConfigField) =>
    configErrors[field] ? INVALID_FIELD_RING : "";

  const handleChange =
    (field: keyof DatabaseConfig) =>
    (
      e: React.ChangeEvent<
        HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
      >,
    ) => {
      onChange(field, e.target.value);
    };

  /**
   * Marks the field as visited on blur, so tabbing into an empty field and
   * leaving it surfaces only that field's own message.
   */
  const handleBlur =
    (field: keyof DatabaseConfig) => () => {
      onBlurField(field);
    };

  const statusText = connectionStatusLabel[connectionStatus];

  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-white/[0.03]">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
        <SectionLabel title="Database Config" className="mb-0" />
        {statusText && (
          <span
            className={`text-xs font-medium ${
              connectionStatus === "connected"
                ? "text-success-600 dark:text-success-500"
                : connectionStatus === "failed"
                  ? "text-error-600 dark:text-error-500"
                  : "text-gray-500 dark:text-gray-400"
            }`}
          >
            {connectionStatus === "connected" ? "🟢 " : ""}
            {statusText}
          </span>
        )}
      </div>

      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Input
            id="db-host"
            name="host"
            label="Host"
            placeholder="localhost"
            value={config.host}
            onChange={handleChange("host")}
            onBlur={handleBlur("host")}
            error={Boolean(configErrors.host)}
            errorMessage={configErrors.host}
            className={errorRing("host")}
          />
          <Input
            id="db-port"
            name="port"
            label="Port"
            placeholder="3306"
            value={config.port}
            onChange={handleChange("port")}
            onBlur={handleBlur("port")}
            error={Boolean(configErrors.port)}
            errorMessage={configErrors.port}
            className={errorRing("port")}
          />
        </div>

        <Input
          id="db-user"
          name="user"
          label="User"
          placeholder="root"
          autoComplete="off"
          value={config.user}
          onChange={handleChange("user")}
          onBlur={handleBlur("user")}
          error={Boolean(configErrors.user)}
          errorMessage={configErrors.user}
          className={errorRing("user")}
        />

        <Input
          id="db-password"
          name="password"
          type="password"
          label="Password"
          placeholder="••••••••"
          autoComplete="new-password"
          value={config.password}
          onChange={handleChange("password")}
          onBlur={handleBlur("password")}
          error={Boolean(configErrors.password)}
          errorMessage={configErrors.password}
          className={errorRing("password")}
        />

        <Input
          id="db-name"
          name="database"
          label="Database"
          placeholder="my_database"
          value={config.database}
          onChange={handleChange("database")}
          onBlur={handleBlur("database")}
          error={Boolean(configErrors.database)}
          errorMessage={configErrors.database}
          className={errorRing("database")}
        />

        <Checkbox
          id="db-ssl-verify"
          label="Verify SSL certificate"
          checked={config.ssl_verify}
          onChange={(checked) => onChange("ssl_verify", checked)}
        />
      </div>

      <div className="mt-5 flex flex-col gap-3">
        <Button
          type="button"
          variant="outline"
          onClick={onTestConnection}
          disabled={isTestingConnection || isConfigInvalid}
          className="w-full justify-center"
        >
          {isTestingConnection ? "Testing connection…" : "Test Connection"}
        </Button>

        {isConfigInvalid && connectionStatus !== "failed" && (
          <Alert
            variant="warning"
            title="Connection details incomplete"
            description={DATABASE_CONFIG_INCOMPLETE_HINT}
            closable
          />
        )}

        {connectionError && connectionStatus === "failed" && (
          <Alert variant="error" description={connectionError} closable />
        )}

        <Alert
          variant="info"
          icon={<span className="text-base leading-none">💡</span>}
          description={DATABASE_CONFIG_HINT}
        />
      </div>
    </section>
  );
};

export default DatabaseConfigPanel;
