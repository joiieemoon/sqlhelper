/**
 * Reusable SQL editor card.
 * Monospace editing area with a chrome header, SQL label and character count.
 */

import SectionLabel from "../section-label/SectionLabel";

interface SqlQueryEditorProps {
  value: string;
  onChange: (value: string) => void;
  /** Section label shown above the editor. */
  label?: string;
  /** Language badge shown in the editor header. */
  language?: string;
  placeholder?: string;
  rows?: number;
  disabled?: boolean;
  /** Validation message shown under the editor; also tints the border red. */
  error?: string | null;
  className?: string;
}

const SqlQueryEditor: React.FC<SqlQueryEditorProps> = ({
  value,
  onChange,
  label = "SQL Query",
  language = "SQL",
  placeholder = "SELECT * FROM ...",
  rows = 10,
  disabled = false,
  error = null,
  className = "",
}) => {
  return (
    <div className={className}>
      <SectionLabel title={label} className="mb-3" />

      <div
        className={`overflow-hidden rounded-2xl border bg-white dark:bg-white/[0.03] ${
          error
            ? "border-error-500 dark:border-error-500"
            : "border-gray-200 dark:border-gray-800"
        }`}
      >
        {/* Editor chrome */}
        <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
          <div className="flex items-center gap-1.5" aria-hidden="true">
            <span className="h-2.5 w-2.5 rounded-full bg-gray-300 dark:bg-gray-600" />
            <span className="h-2.5 w-2.5 rounded-full bg-gray-300 dark:bg-gray-600" />
            <span className="h-2.5 w-2.5 rounded-full bg-gray-300 dark:bg-gray-600" />
          </div>

          <span className="rounded-md bg-brand-50 px-2 py-0.5 text-theme-xs font-semibold uppercase text-brand-500 dark:bg-brand-500/15 dark:text-brand-400">
            {language}
          </span>
        </div>

        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={rows}
          disabled={disabled}
          spellCheck={false}
          aria-label={`${label} editor`}
          aria-invalid={Boolean(error)}
          className="block w-full resize-y bg-transparent px-4 py-4 font-mono text-sm leading-6 text-gray-800 placeholder:text-gray-400 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-50 dark:text-white/90 dark:placeholder:text-white/30"
        />

        {/* Editor footer */}
        <div className="flex items-center justify-between gap-3 border-t border-gray-100 px-4 py-2 dark:border-gray-800">
          <span
            className={`text-theme-xs ${error ? "text-error-500" : ""}`}
            role={error ? "alert" : undefined}
          >
            {error}
          </span>
          <span className="ml-auto shrink-0 text-theme-xs text-gray-400 dark:text-gray-500">
            {value.length} chars
          </span>
        </div>
      </div>
    </div>
  );
};

export default SqlQueryEditor;
