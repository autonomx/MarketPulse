import type { RpgWorldSummary } from './api/rpgWorldLibraryClient';
import { RpgWorldDossierQualityPanel } from './RpgWorldDossierQualityPanel';

interface RpgWorldAdvancedPanelProps {
  world: RpgWorldSummary;
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function RpgWorldAdvancedPanel({ world }: RpgWorldAdvancedPanelProps) {
  return (
    <section className="rpg-authoring-page" aria-label="Advanced world details">
      <div className="rpg-authoring-page-heading">
        <div>
          <p className="eyebrow">Technical details</p>
          <h2>Advanced</h2>
          <p>Inspect stable identifiers, lifecycle state, provider-independent metadata, and editorial quality.</p>
        </div>
        <span>{world.status}</span>
      </div>
      <section className="rpg-authoring-document-block">
        <h3>World identity</h3>
        <dl className="rpg-authoring-fact-grid">
          <div><dt>Technical ID</dt><dd>{world.id}</dd></div>
          <div><dt>Status</dt><dd>{world.status}</dd></div>
          <div><dt>Source mode</dt><dd>{world.source_mode}</dd></div>
          <div><dt>Draft revision</dt><dd>{world.draft_revision}</dd></div>
          <div><dt>Seed</dt><dd>{world.seed}</dd></div>
          <div><dt>Created</dt><dd>{formatDate(world.created_at)}</dd></div>
          <div><dt>Updated</dt><dd>{formatDate(world.updated_at)}</dd></div>
        </dl>
        <p>The technical ID is assigned by the backend during creation and remains stable across edits, releases, and campaigns.</p>
      </section>
      <RpgWorldDossierQualityPanel worldId={world.id} />
      <details className="rpg-authoring-structured-data">
        <summary>World metadata</summary>
        <pre>{JSON.stringify(world.metadata, null, 2)}</pre>
      </details>
      {world.generation ? (
        <details className="rpg-authoring-structured-data">
          <summary>Latest generation run</summary>
          <pre>{JSON.stringify(world.generation, null, 2)}</pre>
        </details>
      ) : null}
    </section>
  );
}
