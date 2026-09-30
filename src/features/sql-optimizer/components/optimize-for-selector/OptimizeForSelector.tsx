/**
 * "OPTIMIZE FOR" scope selector.
 * Scope chips plus a "+ Custom" chip that opens the custom request modal.
 */

import { useState } from "react";
import { Modal } from "../../../../components/common/modal";
import Input from "../../../../components/form/input/input-fields";
import Button from "../../../../components/ui/button/Button";
import { useModal } from "../../../../hooks/useModal";
import { truncate } from "../../../../utils/helpers";
import type { OptimizeScope } from "../../types/sql-optimizer.types";
import SectionLabel from "../section-label/SectionLabel";

interface OptimizeForSelectorProps {
  scopes: OptimizeScope[];
  selectedScopeIds: string[];
  onToggleScope: (scopeId: string) => void;
  /** Current custom optimization request (empty string when none). */
  customRequest: string;
  onSaveCustomRequest: (request: string) => void;
}

/** Max characters of a custom request shown on the chip. */
const CUSTOM_CHIP_MAX_LENGTH = 26;

const chipBase =
  "inline-flex items-center justify-center rounded-full border px-4 py-2 text-sm font-medium transition";
const chipSelectedClasses =
  "border-brand-500 bg-brand-500 text-white shadow-theme-xs";
const chipIdleClasses =
  "border-gray-300 bg-white text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-white/[0.03] dark:hover:text-gray-300";

const OptimizeForSelector: React.FC<OptimizeForSelectorProps> = ({
  scopes,
  selectedScopeIds,
  onToggleScope,
  customRequest,
  onSaveCustomRequest,
}) => {
  const { isOpen, openModal, closeModal } = useModal();
  const [draftRequest, setDraftRequest] = useState("");

  const hasCustomRequest = customRequest.trim() !== "";

  const openCustomModal = () => {
    setDraftRequest(customRequest);
    openModal();
  };

  const handleDraftChange = (
    e: React.ChangeEvent<
      HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
    >,
  ) => {
    setDraftRequest(e.target.value);
  };

  const handleSaveCustomRequest = () => {
    onSaveCustomRequest(draftRequest);
    closeModal();
  };

  const handleRemoveCustomRequest = () => {
    onSaveCustomRequest("");
    closeModal();
  };

  return (
    <div>
      <SectionLabel title="Optimize For" className="mb-3" />

      <div className="flex flex-wrap items-center gap-2">
        {scopes.map((scope) => {
          const isSelected = selectedScopeIds.includes(scope.id);

          return (
            <button
              key={scope.id}
              type="button"
              aria-pressed={isSelected}
              onClick={() => onToggleScope(scope.id)}
              className={`${chipBase} ${
                isSelected ? chipSelectedClasses : chipIdleClasses
              }`}
            >
              {scope.label}
            </button>
          );
        })}

        <button
          type="button"
          aria-haspopup="dialog"
          onClick={openCustomModal}
          className={`${chipBase} ${
            hasCustomRequest ? chipSelectedClasses : chipIdleClasses
          }`}
        >
          {hasCustomRequest
            ? truncate(customRequest, CUSTOM_CHIP_MAX_LENGTH)
            : "+ Custom"}
        </button>
      </div>

      <Modal
        isOpen={isOpen}
        onClose={closeModal}
        size="sm"
        footer={
          <div className="flex items-center justify-end gap-3 px-6 pb-6">
            {hasCustomRequest && (
              <button
                type="button"
                onClick={handleRemoveCustomRequest}
                className="rounded-lg px-4 py-3 text-sm font-medium text-error-600 transition hover:text-error-500 dark:text-error-400"
              >
                Remove
              </button>
            )}
            <Button variant="outline" size="sm" onClick={closeModal}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSaveCustomRequest}
              disabled={!draftRequest.trim()}
            >
              Save
            </Button>
          </div>
        }
      >
        <div className="px-6 pt-6">
          <h3 className="text-lg font-semibold text-gray-800 dark:text-white/90">
            Custom optimization request
          </h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Describe what the optimizer should focus on for this query. Sent as
            the <code>custom_request</code> field.
          </p>

          <div className="mt-4">
            <Input
              id="custom-optimize-request"
              name="custom-optimize-request"
              label="Request"
              placeholder="e.g. Replace this filter with a covering index"
              value={draftRequest}
              onChange={handleDraftChange}
            />
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default OptimizeForSelector;
