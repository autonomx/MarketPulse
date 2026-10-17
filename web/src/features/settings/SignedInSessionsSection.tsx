import { useCallback, useEffect, useState } from 'react';
import {
  AuthRequestError,
  fetchAuthSession,
  listAuthSessions,
  oidcLoginUrl,
  revokeAuthSessions,
  type AuthSession,
  type AuthSessionSummary,
} from '../../api/authClient';
import { SettingsSection } from './SettingsPrimitives';

function when(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function failureMessage(error: unknown, mode: AuthSession['mode']): string {
  if (error instanceof AuthRequestError && error.status === 401) {
    return mode === 'local' ? 'That password (or install credential) is not correct.' : 'Sign in again, then retry within ten minutes.';
  }
  if (error instanceof AuthRequestError && error.status === 429) return 'Too many attempts. Wait a minute and retry.';
  return 'The sessions could not be changed.';
}

/** The signed-in user's sessions; other devices can be signed out (ASVS 3.3.4). Hidden while sign-in is off. */
export function SignedInSessionsSection() {
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [sessions, setSessions] = useState<AuthSessionSummary[]>([]);
  const [credential, setCredential] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    void listAuthSessions().then(setSessions).catch(() => setSessions([]));
  }, []);

  useEffect(() => {
    let active = true;
    void fetchAuthSession()
      .then((session) => {
        if (!active || !session.enforced || !session.authenticated) return;
        setAuth(session);
        reload();
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [reload]);

  if (!auth) return null;
  const local = auth.mode === 'local';

  function revoke(sessionId: string | null): void {
    setBusy(true);
    setMessage(null);
    void revokeAuthSessions({ session_id: sessionId, credential: local ? credential : null })
      .then((count) => {
        setMessage(count === 1 ? 'Signed out 1 session.' : `Signed out ${count} sessions.`);
        setCredential('');
        reload();
      })
      .catch((error: unknown) => setMessage(failureMessage(error, auth?.mode ?? 'local')))
      .finally(() => setBusy(false));
  }

  const others = sessions.filter((session) => !session.current);
  return (
    <SettingsSection title="Signed-in sessions" scope="session" description="Browsers signed in to your account.">
      <ul className="settings-session-list" aria-label="Signed-in sessions">
        {sessions.map((session) => (
          <li key={session.id}>
            <span>{session.current ? 'This browser' : `Session ${session.id.slice(0, 6)}`} ({session.auth_method})</span>
            <span>Last active {when(session.last_seen_at)}; signed in {when(session.created_at)}</span>
            {session.current ? null : (
              <button type="button" disabled={busy || (local && !credential)} onClick={() => revoke(session.id)}>
                Sign out
              </button>
            )}
          </li>
        ))}
      </ul>
      {local ? (
        <label>
          <span>Your password, or the install credential (required to sign out other sessions)</span>
          <input type="password" autoComplete="current-password" value={credential} onChange={(event) => setCredential(event.currentTarget.value)} />
        </label>
      ) : (
        <p>Signing out other sessions needs a sign-in in the last ten minutes. <a href={oidcLoginUrl('/settings')}>Sign in again</a></p>
      )}
      <button type="button" disabled={busy || !others.length || (local && !credential)} onClick={() => revoke(null)}>
        Sign out all other sessions
      </button>
      {message ? <p role="status">{message}</p> : null}
    </SettingsSection>
  );
}
