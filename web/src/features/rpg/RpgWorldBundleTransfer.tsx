import { useEffect, useState } from 'react';
import { rpgWorldBundleClient } from './api/rpgWorldBundleClient';
import { rpgWorldLibraryClient } from './api/rpgWorldLibraryClient';
import './RpgWorldBundleTransfer.css';
import { downloadBlob } from '../../shared/download';

interface RpgWorldBundleTransferProps {
  initialWorldId?: string;
  onImported?: (worldId: string) => Promise<void> | void;
}

interface WorldOption {
  id: string;
  title: string;
}

export function RpgWorldBundleTransfer({
  initialWorldId = '',
  onImported,
}: RpgWorldBundleTransferProps) {
  const [exportWorldId, setExportWorldId] = useState(initialWorldId);
  const [importFile, setImportFile] = useState<File>();
  const [targetWorldId, setTargetWorldId] = useState('');
  const [fileInputKey, setFileInputKey] = useState(0);
  const [worldOptions, setWorldOptions] = useState<WorldOption[]>([]);
  const [worldsLoading, setWorldsLoading] = useState(true);
  const [worldsError, setWorldsError] = useState<string>();
  const [feedback, setFeedback] = useState<string>();
  const [error, setError] = useState<string>();
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    if (initialWorldId) setExportWorldId(initialWorldId);
  }, [initialWorldId]);

  useEffect(() => {
    let active = true;
    void rpgWorldLibraryClient.list()
      .then((result) => {
        if (!active) return;
        const options = result.worlds.map((world) => ({ id: world.id, title: world.title }));
        setWorldOptions(options);
        setExportWorldId((current) => (
          options.some((world) => world.id === current)
            ? current
            : options[0]?.id ?? ''
        ));
        setWorldsError(undefined);
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setWorldsError(cause instanceof Error ? cause.message : 'Worlds could not be loaded.');
      })
      .finally(() => {
        if (active) setWorldsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const exportWorld = async () => {
    const worldId = exportWorldId.trim();
    if (!worldId || exporting) return;
    setExporting(true);
    try {
      const download = await rpgWorldBundleClient.exportWorld(worldId);
      downloadBlob(download.blob, download.filename);
      setFeedback(`World bundle exported: ${download.filename}`);
      setError(undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'World export failed.');
    } finally {
      setExporting(false);
    }
  };

  const importWorld = async () => {
    if (!importFile || importing) return;
    setImporting(true);
    try {
      const result = await rpgWorldBundleClient.importWorld(importFile, targetWorldId);
      const imageCount = (result.counts.images_created ?? 0) + (result.counts.images_reused ?? 0);
      const launchStatus = result.launch_preparation?.status;
      setFeedback(
        `World imported: ${result.world_id} • ${result.counts.map_definitions ?? 0} maps • ${imageCount} images`,
      );
      if (launchStatus === 'generating') {
        setFeedback(`World imported: ${result.world_id}. Launch preparation has started.`);
      } else if (launchStatus === 'ready') {
        setFeedback(`World imported: ${result.world_id}. Opening scenarios are ready to play.`);
      } else if (launchStatus === 'recovery_required') {
        setFeedback(`World imported: ${result.world_id}. Open Campaign Setup to finish launch recovery.`);
      }
      setError(undefined);
      setExportWorldId(result.world_id);
      setWorldOptions((current) => current.some((world) => world.id === result.world_id)
        ? current
        : [...current, { id: result.world_id, title: result.world_id }]);
      setImportFile(undefined);
      setFileInputKey((value) => value + 1);
      setTargetWorldId('');
      await onImported?.(result.world_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'World import failed.');
    } finally {
      setImporting(false);
    }
  };

  return (
    <section
      className="rpg-world-library-panel rpg-world-library-form rpg-world-bundle-transfer"
      aria-label="World export and import"
    >
      <div className="rpg-world-bundle-heading">
        <p className="eyebrow">Portable bundle</p>
        <h3>Export / import world</h3>
        <p>
          Transfer world canon, topic history, scenarios, map blueprints, compiled maps,
          releases, and referenced images in one checksummed archive.
        </p>
      </div>
      <div className="rpg-world-bundle-grid">
        <section className="rpg-world-bundle-card" aria-labelledby="world-bundle-export-title">
          <div className="rpg-world-bundle-card-heading">
            <span className="rpg-world-bundle-step" aria-hidden="true">1</span>
            <div>
              <h4 id="world-bundle-export-title">Export a world</h4>
              <p>Download a self-contained archive of the selected world.</p>
            </div>
          </div>
          <label>
            <span>World to export</span>
            <select
              aria-label="World to export"
              value={exportWorldId}
              onChange={(event) => setExportWorldId(event.currentTarget.value)}
              disabled={worldsLoading || !worldOptions.length}
            >
              <option value="">
                {worldsLoading ? 'Loading worlds…' : 'No worlds available'}
              </option>
              {worldOptions.map((world) => (
                <option key={world.id} value={world.id}>{world.title} ({world.id})</option>
              ))}
            </select>
          </label>
          {worldsError ? <p className="rpg-world-bundle-load-error">{worldsError}</p> : null}
          <button
            type="button"
            className="rpg-secondary-button"
            disabled={!exportWorldId.trim() || exporting || worldsLoading}
            onClick={() => void exportWorld()}
          >
            {exporting ? 'Preparing export…' : 'Export world bundle'}
          </button>
        </section>
        <section className="rpg-world-bundle-card" aria-labelledby="world-bundle-import-title">
          <div className="rpg-world-bundle-card-heading">
            <span className="rpg-world-bundle-step" aria-hidden="true">2</span>
            <div>
              <h4 id="world-bundle-import-title">Import a bundle</h4>
              <p>Restore it as a new world without changing the original.</p>
            </div>
          </div>
          <label className="rpg-world-bundle-file-picker">
            <span>World bundle</span>
            <span className="rpg-world-bundle-file-control">
              <span className="rpg-world-bundle-file-button">Choose bundle</span>
              <span className="rpg-world-bundle-file-name">{importFile?.name ?? 'No file selected'}</span>
            </span>
            <input
              key={fileInputKey}
              aria-label="World bundle file"
              type="file"
              accept=".zip,.omnix-world.zip,application/zip"
              onChange={(event) => setImportFile(event.currentTarget.files?.[0])}
            />
          </label>
          <label>
            <span>New world id <small>Optional</small></span>
            <input
              aria-label="New world id (optional)"
              value={targetWorldId}
              placeholder="Use bundle world id"
              onChange={(event) => setTargetWorldId(event.currentTarget.value)}
            />
          </label>
          <button
            type="button"
            disabled={!importFile || importing}
            onClick={() => void importWorld()}
          >
            {importing ? 'Validating and importing…' : 'Import world bundle'}
          </button>
        </section>
      </div>
      <small className="rpg-world-bundle-note">
        Imports never overwrite an existing world. Use a new world id to create a portable clone.
      </small>
      {feedback ? <p className="rpg-world-library-feedback" aria-live="polite">{feedback}</p> : null}
      {error ? <p className="rpg-world-library-error" aria-live="assertive">{error}</p> : null}
    </section>
  );
}
