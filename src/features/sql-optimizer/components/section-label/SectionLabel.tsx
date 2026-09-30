/**
 * Section label used above each optimizer panel (e.g. "SQL QUERY").
 */

interface SectionLabelProps {
  title: string;
  className?: string;
}

const SectionLabel: React.FC<SectionLabelProps> = ({
  title,
  className = "",
}) => {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <span
        className="h-3.5 w-1 rounded-full bg-brand-500"
        aria-hidden="true"
      />
      <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
        {title}
      </h2>
    </div>
  );
};

export default SectionLabel;
