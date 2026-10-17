import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceImportButton } from './WorkspaceImportButton';

afterEach(() => vi.restoreAllMocks());

async function choose(text: string, name = 'swing.json') {
  const file = new File([text], name, { type: 'application/json' });
  // jsdom's File has no text(); browsers do.
  Object.defineProperty(file, 'text', { value: async () => text });
  await act(async () => {
    fireEvent.change(screen.getByLabelText('Workspace file to import'), { target: { files: [file] } });
  });
}

describe('workspace import', () => {
  it('asks for a name, offering the file’s, and imports under it', async () => {
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('Swing setups');
    const onImport = vi.fn(async () => 'imported' as const);
    render(<WorkspaceImportButton onImport={onImport} />);
    await choose(JSON.stringify({ schemaVersion: 2, layout: 'columns-1' }));
    expect(prompt).toHaveBeenCalledWith('Import workspace as', 'swing');
    expect(onImport).toHaveBeenCalledWith({ schemaVersion: 2, layout: 'columns-1' }, 'Swing setups');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says why a file was refused, and imports nothing when the name is cancelled', async () => {
    vi.spyOn(window, 'prompt').mockReturnValueOnce(null).mockReturnValue('Other');
    const onImport = vi.fn(async () => 'invalid' as const);
    render(<WorkspaceImportButton onImport={onImport} />);
    await choose('not json');
    expect(screen.getByRole('alert')).toHaveTextContent('swing.json is not a JSON file.');
    await choose('{}');
    expect(onImport).not.toHaveBeenCalled();
    await choose('{}', 'other.json');
    expect(screen.getByRole('alert')).toHaveTextContent('other.json is not an Omnix workspace export.');
  });
});
