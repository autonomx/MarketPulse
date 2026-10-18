import { useCallback, useEffect, useState } from 'react';
import { unwrap } from '../src/api/http';
import { api } from '../src/features/trading/api/gateway';
import type { components } from '../src/features/trading/api/generated';
import { SettingsSection } from '../src/features/settings/SettingsPrimitives';

type Status = components['schemas']['ResearchRepositoryCredentialStatus'];

/**
 * Where premarket research handoffs come from (Omnix S-7): Omnix's own public repository (D-9), and an optional
 * read-only token for GitHub's rate limit.
 * The owner sets the token; the gateway never returns it.
 */
export function ResearchHandoffSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [token, setToken] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    void unwrap(api.GET('/api/trading/prospective-gap/github-credentials', { cache: 'no-store' }))
      .then(setStatus)
      .catch(() => setMessage('Only the owner can see the research source.'));
  }, []);
  useEffect(load, [load]);

  const save = (body: components['schemas']['ResearchRepositoryCredentialUpdate']) => {
    setBusy(true);
    setMessage(null);
    void unwrap(api.PUT('/api/trading/prospective-gap/github-credentials', { body }))
      .then((next) => {
        setStatus(next);
        setToken('');
        setMessage(next.token_configured ? 'Token saved.' : 'Token removed.');
      })
      .catch(() => setMessage('The token could not be saved.'))
      .finally(() => setBusy(false));
  };

  return (
    <SettingsSection
      title="Source"
      description="The premarket research handoffs and the gap climatology, published by Omnix's research runs to a public repository."
    >
      {status ? (
        <>
          <dl className="mp-settings-facts">
            <div>
              <dt>Repository</dt>
              <dd><code>{status.repository}@{status.ref}</code><span className="mp-settings-tag">{status.transport === 'gh' ? 'GitHub CLI' : 'GitHub API'}</span></dd>
            </div>
            <div>
              <dt>Schedule</dt>
              <dd>Imported each trading morning, 04:00–09:30 ET</dd>
            </div>
            <div>
              <dt>Read-only token</dt>
              <dd>{status.token_configured ? `Saved (${status.token_masked})` : 'Not set'}</dd>
            </div>
          </dl>
          {status.token_editable ? (
            <div className="settings-field">
              <span className="settings-field-label">Token</span>
              <form className="mp-settings-inline" onSubmit={(event) => { event.preventDefault(); save({ token }); }}>
                <input type="password" aria-label="Research repository token" placeholder="github_pat_…" value={token}
                  autoComplete="off" maxLength={500} onChange={(event) => setToken(event.target.value)} />
                <button type="submit" className="settings-primary-button" disabled={busy || !token.trim()}>Save token</button>
                {status.token_configured ? <button type="button" className="settings-secondary-button" disabled={busy} onClick={() => save({ clear_token: true })}>Remove</button> : null}
              </form>
              <span className="settings-field-help">Optional. The repository is public; a fine-grained token with read access to contents only raises GitHub&apos;s rate limit.</span>
            </div>
          ) : <p className="mp-settings-note">The token comes from the environment and is not editable here.</p>}
        </>
      ) : null}
      {message ? <p role="status" className="mp-settings-note">{message}</p> : null}
    </SettingsSection>
  );
}
