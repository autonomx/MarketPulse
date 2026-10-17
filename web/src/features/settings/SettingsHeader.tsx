export function SettingsHeader({ dirtyCount, saving, onDiscard, onSave }: {
  dirtyCount: number;
  saving?: boolean;
  onDiscard: () => void;
  onSave: () => void;
}) {
  const status = saving ? 'Saving changes' : dirtyCount ? `${dirtyCount} unsaved ${dirtyCount === 1 ? 'change' : 'changes'}` : 'All changes saved';
  return (
    <header className="settings-control-header">
      <div className="settings-title-block">
        <p><span>Omnix</span> / Settings</p>
        <h1>Settings Control Center</h1>
        <small>Manage providers, models, services, modules, and assistant behavior.</small>
      </div>
      <div className="settings-save-actions">
        <span role="status">{status}</span>
        <button type="button" onClick={onDiscard} disabled={!dirtyCount || saving}>Discard</button>
        <button type="button" className="settings-primary-button" onClick={onSave} disabled={!dirtyCount || saving}>{saving ? 'Saving...' : 'Save changes'}</button>
      </div>
    </header>
  );
}
