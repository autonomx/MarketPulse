/** Imports a workspace file written by Export as a new workspace. */
import { useRef, useState } from 'react';
import type { WorkspaceImportResult } from './useTradingWorkspacePersistence';
import { PRODUCT_NAME_WITH_ARTICLE } from '../../../shared/productIdentity';

type Props = {
  disabled?: boolean;
  onImport: (value: unknown, name: string) => Promise<WorkspaceImportResult>;
};

function suggestedName(value: unknown, fileName: string): string {
  const name = value && typeof value === 'object' ? (value as { name?: unknown }).name : undefined;
  return typeof name === 'string' && name.trim() ? name.trim() : fileName.replace(/\.json$/i, '');
}

export function WorkspaceImportButton({ disabled, onImport }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const read = async (file: File) => {
    let value: unknown;
    try {
      value = JSON.parse(await file.text());
    } catch {
      setError(`${file.name} is not a JSON file.`);
      return;
    }
    const name = window.prompt('Import workspace as', suggestedName(value, file.name));
    if (!name?.trim()) return;
    const result = await onImport(value, name);
    if (result === 'invalid') setError(`${file.name} is not ${PRODUCT_NAME_WITH_ARTICLE} workspace export.`);
    else if (result === 'error') setError('The imported workspace could not be saved.');
    else setError('');
  };
  return (
    <>
      <button type="button" onClick={() => inputRef.current?.click()} disabled={disabled}>Import</button>
      <input
        ref={inputRef}
        type="file"
        accept="application/json,.json"
        aria-label="Workspace file to import"
        hidden
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = '';
          if (file) void read(file);
        }}
      />
      {error ? <span role="alert" className="workspace-error">{error}</span> : null}
    </>
  );
}
