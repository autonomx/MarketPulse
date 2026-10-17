import {
  Alert,
  Anchor,
  Button,
  Center,
  Checkbox,
  Divider,
  Group,
  Paper,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useEffect, useState, type FormEvent } from 'react';
import {
  AuthRequestError,
  fetchAuthSession,
  googleLoginUrl,
  inviteUsable,
  loginWithInstallCredential,
  loginWithPassword,
  oidcLoginUrl,
  register,
  signInAsGuest,
  type AuthOptions,
  type AuthSession,
} from '../api/authClient';
import { desktopBridge } from '../shared/desktopBridge';
import { PRODUCT_NAME, PRODUCT_NAME_WITH_ARTICLE } from '../shared/productIdentity';
import { desktopGoogleSignIn } from './desktopGoogle';

const UNREACHABLE = `${PRODUCT_NAME} is not reachable. Check that the gateway is running.`;

const ERROR_MESSAGES: Record<string, string> = {
  login_link_expired: `That sign-in link has expired or was already used. Open ${PRODUCT_NAME} from the launcher again, or sign in below.`,
  sign_in_failed: 'Sign-in did not complete. Try again.',
  invalid_credential: 'That install credential is not valid.',
  invalid_credentials: "That email and password don't match an account.",
  email_taken: 'An account with this email already exists. Sign in instead.',
  invalid_email: 'Enter a valid email address.',
  password_is_email: "Choose a password that isn't your email address.",
  registration_closed: `This ${PRODUCT_NAME} doesn't take new accounts. Ask its owner for an invite link.`,
  invite_required: 'Creating an account here needs an invite link from the owner.',
  invite_invalid: 'That invite link has expired or was already used. Ask for a new one.',
  guests_disabled: 'Guest access is turned off here.',
  google_email_in_use:
    `That Google account's email already belongs to ${PRODUCT_NAME_WITH_ARTICLE} account. Sign in with your password, then connect Google in Settings.`,
  google_not_allowed: "That Google account isn't allowed to sign in here.",
  google_not_configured: 'Sign-in with Google is not set up here.',
  rate_limited: 'Too many attempts. Wait a minute and try again.',
};

type Mode = 'sign-in' | 'create';

// Only same-origin absolute paths; mirrors the gateway's redirect rule.
export function safeNextPath(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return '/';
  return value;
}

function goTo(path: string): void {
  window.location.assign(path);
}

function messageFor(caught: unknown, fallback: string, minLength: number): string {
  if (!(caught instanceof AuthRequestError)) return UNREACHABLE;
  if (caught.status === 429) return ERROR_MESSAGES.rate_limited;
  if (caught.detail === 'password_too_short') return `Use a password of at least ${minLength} characters.`;
  return (caught.detail && ERROR_MESSAGES[caught.detail]) || fallback;
}

/** Existing account or new account. */
function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  return (
    <Group grow gap="xs" role="group" aria-label="Sign in or create an account">
      {(['sign-in', 'create'] as const).map((value) => (
        <Button key={value} type="button" variant={mode === value ? 'filled' : 'default'} aria-pressed={mode === value}
          onClick={() => onChange(value)}>
          {value === 'create' ? 'New account' : 'Existing account'}
        </Button>
      ))}
    </Group>
  );
}

/** Google, guest access, or why there is no sign-up here. */
function OtherWaysIn({ options, invite, canCreate, googleHref, onDesktopGoogle, googleWaiting, submitting, onGuest }: {
  options: AuthOptions;
  invite: string | null;
  canCreate: boolean;
  googleHref: string;
  /** Inside the desktop app: Google signs in through the system browser (MarketPulse S-5). */
  onDesktopGoogle?: () => void;
  googleWaiting?: boolean;
  submitting: boolean;
  onGuest: () => void;
}) {
  const guests = options.guests && !invite;
  return (
    <>
      {options.google || guests ? <Divider label="or" labelPosition="center" /> : null}
      {options.google && onDesktopGoogle ? (
        <>
          <Button variant="default" loading={googleWaiting} onClick={onDesktopGoogle}>
            Continue with Google
          </Button>
          {googleWaiting ? (
            <Text size="sm" c="dimmed" role="status">Finish signing in with Google in your browser, then come back here.</Text>
          ) : null}
        </>
      ) : options.google ? (
        <Button component="a" variant="default" href={googleHref}>
          Continue with Google
        </Button>
      ) : null}
      {guests ? (
        <Button variant="subtle" loading={submitting} onClick={onGuest}>
          Continue as guest
        </Button>
      ) : null}
      {!canCreate && options.registration !== 'open' && !invite ? (
        <Text size="sm" c="dimmed">
          {options.registration === 'closed' ? ERROR_MESSAGES.registration_closed : ERROR_MESSAGES.invite_required}
        </Text>
      ) : null}
    </>
  );
}

/** The owner's way in from another browser: the install credential (WP-4.1). */
function InstallCredentialSignIn({ submitting, onSubmit }: { submitting: boolean; onSubmit: (credential: string) => void }) {
  const [open, setOpen] = useState(false);
  const [credential, setCredential] = useState('');
  return (
    <>
      <Text size="sm" c="dimmed">
        On this computer, opening {PRODUCT_NAME} from the launcher signs you in automatically.{' '}
        <Anchor component="button" type="button" size="sm" onClick={() => setOpen((value) => !value)}>
          Use the install credential
        </Anchor>
      </Text>
      {open ? (
        <form
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            onSubmit(credential.trim());
          }}
        >
          <Stack gap="sm">
            <Text size="sm" c="dimmed">
              The owner can sign in with the install credential shown by{' '}
              <code>python -m app.security show-install-credential</code>.
            </Text>
            <PasswordInput
              label="Install credential"
              value={credential}
              onChange={(event) => setCredential(event.currentTarget.value)}
              autoComplete="off"
            />
            <Button type="submit" variant="light" loading={submitting} disabled={!credential.trim()}>
              Sign in with the credential
            </Button>
          </Stack>
        </form>
      ) : null}
    </>
  );
}

export function LoginPage() {
  const params = new URLSearchParams(window.location.search);
  const next = safeNextPath(params.get('next'));
  const invite = params.get('invite');
  const [session, setSession] = useState<AuthSession | null>(null);
  const [mode, setMode] = useState<Mode>(invite ? 'create' : 'sign-in');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(true);
  const [inviteOk, setInviteOk] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(ERROR_MESSAGES[params.get('error') ?? ''] ?? null);
  const [submitting, setSubmitting] = useState(false);
  const [googleWaiting, setGoogleWaiting] = useState(false);
  const desktop = desktopBridge();

  useEffect(() => {
    let cancelled = false;
    void fetchAuthSession()
      .then((payload) => {
        if (cancelled) return;
        if (payload.authenticated || !payload.enforced) {
          goTo(next);
          return;
        }
        setSession(payload);
      })
      .catch(() => {
        if (!cancelled) setError(UNREACHABLE);
      });
    if (invite) {
      void inviteUsable(invite)
        .then((usable) => !cancelled && setInviteOk(usable))
        .catch(() => !cancelled && setInviteOk(null));
    }
    return () => {
      cancelled = true;
    };
  }, [next, invite]);

  const options = session?.options;
  const minLength = options?.min_password_length ?? 12;
  const canCreate = Boolean(options && (options.registration === 'open' || (invite && inviteOk !== false)));

  const run = async (action: () => Promise<unknown>, fallback: string) => {
    setSubmitting(true);
    setError(null);
    try {
      await action();
      goTo(next);
    } catch (caught) {
      setError(messageFor(caught, fallback, minLength));
    } finally {
      setSubmitting(false);
    }
  };

  const submitAccount = (event: FormEvent) => {
    event.preventDefault();
    if (mode === 'sign-in') {
      void run(() => loginWithPassword({ email: email.trim(), password, remember }), ERROR_MESSAGES.invalid_credentials);
    } else {
      void run(
        () => register({ email: email.trim(), password, display_name: name.trim() || null, invite, remember }),
        'The account could not be created.',
      );
    }
  };

  return (
    <Center mih="100vh" p="md">
      <Paper withBorder p="xl" radius="md" maw={440} w="100%">
        <Stack gap="md">
          <Title order={2}>{mode === 'create' ? `Create your ${PRODUCT_NAME} account` : `Sign in to ${PRODUCT_NAME}`}</Title>
          {error ? (
            <Alert color="red" role="alert">
              {error}
            </Alert>
          ) : null}
          {invite && inviteOk ? <Alert color="blue">You were invited. Create your account to join.</Alert> : null}
          {invite && inviteOk === false ? (
            <Alert color="yellow">{ERROR_MESSAGES.invite_invalid}</Alert>
          ) : null}

          {session?.mode === 'oidc' ? (
            <Button component="a" href={oidcLoginUrl(next)}>
              Sign in with your organization
            </Button>
          ) : null}

          {session?.mode === 'local' ? (
            <>
              {canCreate ? (
                <ModeToggle
                  mode={mode}
                  onChange={(value) => {
                    setMode(value);
                    setError(null);
                  }}
                />
              ) : null}
              <form onSubmit={submitAccount}>
                <Stack gap="sm">
                  {mode === 'create' ? (
                    <TextInput label="Name" value={name} onChange={(event) => setName(event.currentTarget.value)}
                      autoComplete="name" placeholder="Optional" />
                  ) : null}
                  <TextInput label="Email" type="email" value={email} required autoComplete="email"
                    onChange={(event) => setEmail(event.currentTarget.value)} />
                  <PasswordInput
                    label="Password"
                    value={password}
                    required
                    autoComplete={mode === 'create' ? 'new-password' : 'current-password'}
                    description={mode === 'create' ? `At least ${minLength} characters. A short sentence works well.` : undefined}
                    onChange={(event) => setPassword(event.currentTarget.value)}
                  />
                  <Checkbox label="Stay signed in on this device" checked={remember}
                    onChange={(event) => setRemember(event.currentTarget.checked)} />
                  <Button type="submit" loading={submitting} disabled={!email.trim() || !password}>
                    {mode === 'create' ? 'Create account' : 'Sign in'}
                  </Button>
                </Stack>
              </form>

              {options ? (
                <OtherWaysIn
                  options={options}
                  invite={invite}
                  canCreate={canCreate}
                  googleHref={googleLoginUrl(next, { invite, remember })}
                  onDesktopGoogle={desktop && options.google_desktop ? () => {
                    setError(null);
                    setGoogleWaiting(true);
                    void desktopGoogleSignIn(desktop, { next, invite, remember })
                      .then((handed) => goTo(handed.next))
                      .catch((caught: unknown) => setError(messageFor(caught, ERROR_MESSAGES.sign_in_failed, minLength)))
                      .finally(() => setGoogleWaiting(false));
                  } : undefined}
                  googleWaiting={googleWaiting}
                  submitting={submitting}
                  onGuest={() => void run(signInAsGuest, ERROR_MESSAGES.guests_disabled)}
                />
              ) : null}
              <InstallCredentialSignIn
                submitting={submitting}
                onSubmit={(credential) => void run(() => loginWithInstallCredential(credential, remember), ERROR_MESSAGES.invalid_credential)}
              />
            </>
          ) : null}
        </Stack>
      </Paper>
    </Center>
  );
}
