import { useCallback, useEffect, useState } from 'react';
import { unwrap } from '../src/api/http';
import { api } from '../src/features/trading/api/gateway';
import type { components } from '../src/features/trading/api/generated';
import { SettingsField, SettingsSection } from '../src/features/settings/SettingsPrimitives';

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
    <SettingsSection title="Research handoffs" scope="global">
      <p className="mp-settings-note">
        Premarket research handoffs and the gap climatology are imported each morning (04:00–09:30 ET) from the
        repository below, the one Omnix publishes them to. It is public, so no token is needed; a read-only token
        only raises GitHub&apos;s rate limit.
      </p>
      {status ? (
        <>
          <SettingsField label="Repository">
            <code>{status.repository}@{status.ref}</code> <small>({status.transport === 'gh' ? 'GitHub CLI' : 'GitHub API'})</small>
          </SettingsField>
          <SettingsField label="Read-only token">
            <span>{status.token_configured ? `Saved (${status.token_masked})` : 'Not set'}</span>
          </SettingsField>
          {status.token_editable ? (
            <form className="mp-settings-inline" onSubmit={(event) => { event.preventDefault(); save({ token }); }}>
              <input type="password" aria-label="Research repository token" placeholder="Optional fine-grained token, contents: read" value={token}
                autoComplete="off" maxLength={500} onChange={(event) => setToken(event.target.value)} />
              <button type="submit" disabled={busy || !token.trim()}>Save token</button>
              {status.token_configured ? <button type="button" disabled={busy} onClick={() => save({ clear_token: true })}>Remove</button> : null}
            </form>
          ) : <p className="mp-settings-note">The token comes from the environment and is not editable here.</p>}
        </>
      ) : null}
      {message ? <p role="status" className="mp-settings-note">{message}</p> : null}
    </SettingsSection>
  );
}
