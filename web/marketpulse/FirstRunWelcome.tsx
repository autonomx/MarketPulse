import { Alert, Button, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { useEffect, useState, type FormEvent } from 'react';
import { AuthRequestError, changeEmail, changePassword, fetchAuthSession } from '../src/api/authClient';
import { desktopBridge } from '../src/shared/desktopBridge';

const MIN_LENGTH = 12;

function reason(caught: unknown): string {
  if (caught instanceof AuthRequestError) {
    if (caught.detail === 'password_too_short') return `Use a password of at least ${MIN_LENGTH} characters.`;
    if (caught.detail === 'password_is_email') return "Choose a password that isn't your email address.";
    if (caught.detail === 'invalid_email') return 'Enter a valid email address.';
    if (caught.detail === 'email_taken') return 'That email already belongs to another account here.';
    if (caught.detail === 'recent_sign_in_required') {
      return 'This sign-in is too old to set the first password. Get a new sign-in code below.';
    }
  }
  return 'That did not work. Try again.';
}

/**
 * First run (roadmap §4.7): the desktop app signed this window in as the owner with a one-time code. The owner sets
 * an email and a first password here; until a password exists, every launch signs the owner in this way again.
 */
export function FirstRunWelcome() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    void fetchAuthSession().then((session) => {
      if (!session.authenticated) window.location.assign('/login');
      else if (session.account?.has_password) window.location.assign('/trading');
    }).catch(() => undefined);
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    void (async () => {
      try {
        await changeEmail({ email: email.trim(), display_name: name.trim() || null });
        await changePassword({ current: null, new_password: password });
        window.location.assign('/trading');
      } catch (caught) {
        setError(reason(caught));
        setExpired(caught instanceof AuthRequestError && caught.detail === 'recent_sign_in_required');
      } finally {
        setSaving(false);
      }
    })();
  };

  const bridge = desktopBridge();
  return (
    <div className="mp-welcome">
      <Paper className="mp-welcome-card" p="xl" radius="md" withBorder>
        <form onSubmit={submit}>
          <Stack gap="md">
            <img src="/marketpulse-icon-128.png" alt="" width={56} height={56} />
            <Title order={2}>Welcome to MarketPulse</Title>
            <Text size="sm" c="dimmed">You are the owner of this install. Choose how you sign in from now on.</Text>
            {error ? <Alert color="red" role="alert">{error}</Alert> : null}
            <TextInput label="Your name" value={name} onChange={(event) => setName(event.currentTarget.value)} autoComplete="name" />
            <TextInput label="Email" type="email" required value={email} onChange={(event) => setEmail(event.currentTarget.value)} autoComplete="email" />
            <PasswordInput label="Password" required description={`At least ${MIN_LENGTH} characters.`} value={password}
              onChange={(event) => setPassword(event.currentTarget.value)} autoComplete="new-password" />
            <Button type="submit" loading={saving} disabled={!email.trim() || password.length < MIN_LENGTH}>Save and continue</Button>
            {expired && bridge ? (
              <Button variant="subtle" onClick={() => void bridge.requestLoginCode()}>Get a new sign-in code</Button>
            ) : null}
          </Stack>
        </form>
      </Paper>
    </div>
  );
}
