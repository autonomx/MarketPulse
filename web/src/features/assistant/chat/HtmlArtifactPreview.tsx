import { useEffect, useMemo, useState } from 'react';
import './HtmlArtifactPreview.css';
import { fetchBytes } from '../../../api/transport';

type PreviewMode = 'preview' | 'source';

export interface HtmlArtifactPreviewsProps {
  runId: string;
  paths: string[];
}

function isHtmlPath(path: string): boolean {
  return /\.html?$/i.test(path.trim());
}

function artifactName(path: string): string {
  const normalized = path.replace(/\\/g, '/');
  return normalized.split('/').filter(Boolean).at(-1) || path;
}

function previewUrl(runId: string, path: string, options: { source?: boolean; download?: boolean; revision?: number } = {}): string {
  const base = `/api/agent-runs/${encodeURIComponent(runId)}/workspace-preview/${path
    .replace(/\\/g, '/')
    .split('/')
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/')}`;
  const query = new URLSearchParams();
  if (options.source) query.set('source', 'true');
  if (options.download) query.set('download', 'true');
  if (options.revision) query.set('v', String(options.revision));
  const suffix = query.toString();
  return suffix ? `${base}?${suffix}` : base;
}

function HtmlArtifactPreviewCard({ runId, path }: { runId: string; path: string }) {
  const [mode, setMode] = useState<PreviewMode>('preview');
  const [source, setSource] = useState('');
  const [sourceState, setSourceState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [revision, setRevision] = useState(1);
  const [fullscreen, setFullscreen] = useState(false);

  const liveUrl = previewUrl(runId, path, { revision });
  const downloadUrl = previewUrl(runId, path, { download: true });

  useEffect(() => {
    if (!fullscreen) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFullscreen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [fullscreen]);

  async function showSource(): Promise<void> {
    setMode('source');
    if (sourceState === 'ready' || sourceState === 'loading') return;
    setSourceState('loading');
    try {
      const response = await fetchBytes(previewUrl(runId, path, { source: true, revision }), { cache: 'no-store' });
      setSource(await response.text());
      setSourceState('ready');
    } catch {
      setSourceState('error');
    }
  }

  function refresh(): void {
    setRevision((value) => value + 1);
    if (mode === 'source') {
      setSource('');
      setSourceState('idle');
      setMode('preview');
    }
  }

  const body = mode === 'source' ? (
    <div className="assistant-html-preview-source" data-source-state={sourceState}>
      {sourceState === 'loading' ? <span>Loading source…</span> : null}
      {sourceState === 'error' ? <span>Unable to load HTML source.</span> : null}
      {sourceState === 'ready' ? <pre><code>{source}</code></pre> : null}
    </div>
  ) : (
    <iframe
      className="assistant-html-preview-frame"
      key={revision}
      loading="lazy"
      referrerPolicy="no-referrer"
      sandbox="allow-scripts"
      src={liveUrl}
      title={`Preview of ${artifactName(path)}`}
    />
  );

  return (
    <article className="assistant-html-preview-card" data-html-artifact-path={path}>
      <header className="assistant-html-preview-toolbar">
        <div className="assistant-html-preview-title">
          <span className="assistant-html-preview-file-icon" aria-hidden="true">&lt;/&gt;</span>
          <div>
            <strong title={path}>{artifactName(path)}</strong>
            <small>HTML · sandboxed preview</small>
          </div>
        </div>
        <div className="assistant-html-preview-actions">
          {mode === 'preview' ? (
            <button type="button" onClick={() => void showSource()}>Show code</button>
          ) : (
            <button type="button" onClick={() => setMode('preview')}>Preview</button>
          )}
          <button type="button" aria-label={`Refresh ${artifactName(path)} preview`} onClick={refresh}>↻</button>
          <button type="button" aria-label={`Open ${artifactName(path)} fullscreen`} onClick={() => setFullscreen(true)}>⛶</button>
          <a aria-label={`Download ${artifactName(path)}`} download={artifactName(path)} href={downloadUrl}>↓</a>
        </div>
      </header>
      <div className="assistant-html-preview-body">{body}</div>

      {fullscreen ? (
        <div className="assistant-html-preview-modal" role="dialog" aria-modal="true" aria-label={`Fullscreen preview of ${artifactName(path)}`}>
          <div className="assistant-html-preview-modal-shell">
            <header className="assistant-html-preview-toolbar">
              <div className="assistant-html-preview-title">
                <span className="assistant-html-preview-file-icon" aria-hidden="true">&lt;/&gt;</span>
                <div>
                  <strong>{artifactName(path)}</strong>
                  <small>{path}</small>
                </div>
              </div>
              <div className="assistant-html-preview-actions">
                {mode === 'preview' ? (
                  <button type="button" onClick={() => void showSource()}>Show code</button>
                ) : (
                  <button type="button" onClick={() => setMode('preview')}>Preview</button>
                )}
                <button type="button" aria-label="Close fullscreen preview" onClick={() => setFullscreen(false)}>✕</button>
              </div>
            </header>
            <div className="assistant-html-preview-modal-body">{body}</div>
          </div>
        </div>
      ) : null}
    </article>
  );
}

export function HtmlArtifactPreviews({ runId, paths }: HtmlArtifactPreviewsProps) {
  const htmlPaths = useMemo(
    () => [...new Set(paths.map((path) => path.trim()).filter((path) => path && isHtmlPath(path)))],
    [paths],
  );
  if (!htmlPaths.length) return null;
  return (
    <section className="assistant-html-previews" aria-label="HTML artifact previews">
      {htmlPaths.map((path) => <HtmlArtifactPreviewCard key={path} runId={runId} path={path} />)}
    </section>
  );
}
