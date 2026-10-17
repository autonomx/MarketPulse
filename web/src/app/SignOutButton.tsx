import { useEffect, useState } from 'react';
import { fetchAuthSession, logout } from '../api/authClient';
import { LOGIN_PATH } from './viewApiScope';

// Shown only when the gateway enforces sign-in and this browser has a session.
// A guest also gets a way to keep their work: creating an account in Settings.
export function SignOutButton() {
  const [visible, setVisible] = useState(false);
  const [guest, setGuest] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchAuthSession()
      .then((session) => {
        if (cancelled) return;
        setVisible(session.enforced && session.authenticated);
        setGuest(session.account?.kind === 'guest');
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!visible) return null;
  return (
    <>
      {guest ? (
        <a href="/settings" aria-label="Guest session: create an account to keep your work">
          Guest · Save your account
        </a>
      ) : null}
      <button
        type="button"
        aria-label="Sign out of Omnix"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void logout()
            .catch(() => undefined)
            .finally(() => window.location.assign(LOGIN_PATH));
        }}
      >
        Sign out
      </button>
    </>
  );
}
