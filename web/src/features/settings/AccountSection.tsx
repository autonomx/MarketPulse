import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  AuthRequestError,
  changeEmail,
  changePassword,
  createInvite,
  fetchAuthSession,
  listInvites,
  revokeInvite,
  startGoogleLink,
  upgradeGuest,
  type AuthSession,
  type InviteSummary,
} from '../../api/authClient';
import { desktopGoogleSignIn } from '../../app/desktopGoogle';
import { desktopBridge } from '../../shared/desktopBridge';
import { SettingsSection } from './SettingsPrimitives';

const MESSAGES: Record<string, string> = {
  email_taken: 'Another account already uses that email.',
  invalid_email: 'Enter a valid email address.',
  password_is_email: "Choose a password that isn't your email address.",
  invalid_credential: 'The current password (or install credential) is not correct.',
  recent_sign_in_required: 'Sign in again, then set your password within ten minutes.',
  google_linked_elsewhere: 'That Google account is already connected to another Omnix account.',
  google_not_configured: 'Sign-in with Google is not set up here.',
};

function message(error: unknown, minLength: number, fallback: string): string {
  if (!(error instanceof AuthRequestError)) return fallback;
  if (error.status === 429) return 'Too many attempts. Wait a minute and retry.';
  if (error.detail === 'password_too_short') return `Use a password of at least ${minLength} characters.`;
  return (error.detail && MESSAGES[error.detail]) || fallback;
}

function when(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

type Act = (action: () => Promise<unknown>, done: string, fallback: string) => Promise<void>;

/** A guest keeps everything by turning the account into a full one. */
function GuestUpgradeForm({ minLength, busy, act }: { minLength: number; busy: boolean; act: Act }) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void act(
      () => upgradeGuest({ email: email.trim(), password, display_name: name.trim() || null, remember: true }),
      'Your account is saved. Everything you made as a guest is still here.',
      'The account could not be created.',
    );
  };
  return (
    <form className="settings-account-form" onSubmit={submit} aria-label="Create your account">
      <p>A guest account ends when its session does. Create an account to keep your work; nothing is lost.</p>
      <label><span>Name</span><input value={name} autoComplete="name" onChange={(event) => setName(event.currentTarget.value)} /></label>
      <label><span>Email</span><input type="email" required value={email} autoComplete="email" onChange={(event) => setEmail(event.currentTarget.value)} /></label>
      <label><span>Password (at least {minLength} characters)</span><input type="password" required value={password} autoComplete="new-password" onChange={(event) => setPassword(event.currentTarget.value)} /></label>
      <button type="submit" disabled={busy || !email.trim() || !password}>Create account</button>
    </form>
  );
}

/** Single-use invite links for people who should be able to register (admins). */
function InviteLinks({ invites, busy, act }: { invites: InviteSummary[]; busy: boolean; act: Act }) {
  const [issued, setIssued] = useState<string | null>(null);
  const create = () => {
    void act(async () => {
      const invite = await createInvite(null);
      setIssued(`${window.location.origin}${invite.path}`);
    }, 'Invite link created. It works once, for seven days.', 'The invite link could not be created.');
  };
  return (
    <div className="settings-account-invites">
      <button type="button" disabled={busy} onClick={create}>Create invite link</button>
      {issued ? <p><code>{issued}</code></p> : null}
      {invites.length ? (
        <ul aria-label="Open invites">
          {invites.map((item) => (
            <li key={item.id}>
              <span>Invite {item.id.slice(0, 6)}{item.note ? ` (${item.note})` : ''}, expires {when(item.expires_at)}</span>
              <button type="button" disabled={busy} onClick={() => void act(() => revokeInvite(item.id), 'Invite revoked.', 'The invite could not be revoked.')}>Revoke</button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** The signed-in account: guest upgrade, email, password, Google and invites. Hidden while sign-in is off. */
export function AccountSection() {
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [status, setStatus] = useState<string | null>(() => {
    const code = new URLSearchParams(window.location.search).get('account_error');
    return code ? MESSAGES[code] ?? 'Connecting Google did not complete.' : null;
  });
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState('');
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [invites, setInvites] = useState<InviteSummary[]>([]);

  const reload = useCallback(() => {
    void fetchAuthSession()
      .then((session) => {
        if (!session.enforced || !session.authenticated || session.mode !== 'local') {
          setAuth(null);
          return;
        }
        setAuth(session);
        if (session.roles.includes('admin')) void listInvites().then(setInvites).catch(() => setInvites([]));
      })
      .catch(() => setAuth(null));
  }, []);

  useEffect(reload, [reload]);

  const account = auth?.account;
  if (!auth || !account) return null;
  const minLength = auth.options?.min_password_length ?? 12;

  const act: Act = async (action, done, fallback) => {
    setBusy(true);
    setStatus(null);
    try {
      await action();
      setStatus((previous) => previous ?? done);
      setCurrent('');
      setPassword('');
      reload();
    } catch (error) {
      setStatus(message(error, minLength, fallback));
    } finally {
      setBusy(false);
    }
  };

  const savePassword = (event: FormEvent) => {
    event.preventDefault();
    void act(async () => {
      const signedOut = await changePassword({ current: current || null, new_password: password });
      if (signedOut) setStatus(`Password saved. Signed out ${signedOut} other session${signedOut === 1 ? '' : 's'}.`);
    }, 'Password saved.', 'The password could not be saved.');
  };

  const saveEmail = (event: FormEvent) => {
    event.preventDefault();
    void act(() => changeEmail({ email: email.trim(), display_name: null }), 'Email saved.', 'The email could not be saved.');
  };

  const connectGoogle = () => {
    setBusy(true);
    const desktop = desktopBridge();
    if (desktop) {
      // Inside the desktop app Google runs in the system browser; this window waits for the link (MarketPulse S-5).
      void desktopGoogleSignIn(desktop, { next: '/settings', link: true })
        .then(() => {
          setStatus('Google is connected.');
          reload();
        })
        .catch((error: unknown) => setStatus(message(error, minLength, 'Connecting Google did not finish.')))
        .finally(() => setBusy(false));
      return;
    }
    void startGoogleLink('/settings')
      .then((url) => window.location.assign(url))
      .catch((error: unknown) => {
        setStatus(message(error, minLength, 'Connecting Google did not start.'));
        setBusy(false);
      });
  };

  const guest = account.kind === 'guest';
  return (
    <SettingsSection title="Your account" scope="session"
      description={guest ? 'You are using a guest account.' : `${account.display_name}${account.email ? ` · ${account.email}` : ''}`}>
      {guest ? <GuestUpgradeForm minLength={minLength} busy={busy} act={act} /> : (
        <>
          {!account.email ? (
            <form className="settings-account-form" onSubmit={saveEmail} aria-label="Add an email">
              <p>Add an email to sign in from other devices.</p>
              <label><span>Email</span><input type="email" required value={email} autoComplete="email" onChange={(event) => setEmail(event.currentTarget.value)} /></label>
              <button type="submit" disabled={busy || !email.trim()}>Save email</button>
            </form>
          ) : null}
          <form className="settings-account-form" onSubmit={savePassword} aria-label={account.has_password ? 'Change password' : 'Set a password'}>
            {account.has_password ? (
              <label><span>Current password</span><input type="password" required value={current} autoComplete="current-password" onChange={(event) => setCurrent(event.currentTarget.value)} /></label>
            ) : account.is_owner ? (
              <label><span>Install credential</span><input type="password" required value={current} autoComplete="off" onChange={(event) => setCurrent(event.currentTarget.value)} /></label>
            ) : null}
            <label><span>{account.has_password ? 'New password' : 'Password'} (at least {minLength} characters)</span><input type="password" required value={password} autoComplete="new-password" onChange={(event) => setPassword(event.currentTarget.value)} /></label>
            <button type="submit" disabled={busy || !password}>{account.has_password ? 'Change password' : 'Set password'}</button>
          </form>
          {auth.options?.google ? (
            account.google_linked ? <p>Google is connected: you can sign in with it.</p> : (
              <button type="button" disabled={busy} onClick={connectGoogle}>Connect Google</button>
            )
          ) : null}
          {auth.roles.includes('admin') ? <InviteLinks invites={invites} busy={busy} act={act} /> : null}
        </>
      )}
      {status ? <p role="status">{status}</p> : null}
    </SettingsSection>
  );
}
