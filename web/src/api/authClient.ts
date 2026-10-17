import type { components } from './generated/core';
import { pipelineFetch } from './fetchPipeline';

export type AuthSession = components['schemas']['AuthSessionResponse'];
export type AuthOptions = components['schemas']['AuthOptionsResponse'];
export type AuthAccount = components['schemas']['AccountResponse'];
export type LocalLoginBody = components['schemas']['LocalLoginRequest'];
export type PasswordLoginBody = components['schemas']['PasswordLoginRequest'];
export type RegisterBody = components['schemas']['RegisterRequest'];
export type GuestUpgradeBody = components['schemas']['GuestUpgradeRequest'];
export type PasswordChangeBody = components['schemas']['PasswordChangeRequest'];
export type EmailChangeBody = components['schemas']['EmailChangeRequest'];
export type InviteSummary = components['schemas']['InviteSummaryResponse'];
export type IssuedInvite = components['schemas']['InviteResponse'];
export type AuthSessionSummary = components['schemas']['AuthSessionSummary'];
export type RevokeSessionsBody = components['schemas']['RevokeSessionsRequest'];

/** A refused auth request; ``detail`` is the gateway's reason code (e.g. ``email_taken``). */
export class AuthRequestError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string | null = null,
  ) {
    super(`auth_request_failed:${status}${detail ? `:${detail}` : ''}`);
  }
}

async function failure(response: Response): Promise<AuthRequestError> {
  let detail: string | null = null;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') detail = body.detail;
  } catch {
    // Not JSON: the status alone explains it.
  }
  return new AuthRequestError(response.status, detail);
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  // The view firewall adds X-Omnix-Client and, when signed in, the CSRF header.
  const response = await pipelineFetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await failure(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await pipelineFetch(path, { credentials: 'same-origin' });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

export function fetchAuthSession(): Promise<AuthSession> {
  return getJson<AuthSession>('/api/auth/session');
}

export function loginWithInstallCredential(credential: string, remember = false): Promise<AuthSession> {
  const body: LocalLoginBody = { credential, remember };
  return postJson<AuthSession>('/api/auth/local/login', body);
}

export function loginWithPassword(body: PasswordLoginBody): Promise<AuthSession> {
  return postJson<AuthSession>('/api/auth/password/login', body);
}

export function register(body: RegisterBody): Promise<AuthSession> {
  return postJson<AuthSession>('/api/auth/register', body);
}

export function signInAsGuest(): Promise<AuthSession> {
  return postJson<AuthSession>('/api/auth/guest');
}

export function upgradeGuest(body: GuestUpgradeBody): Promise<AuthSession> {
  return postJson<AuthSession>('/api/auth/guest/upgrade', body);
}

export async function changePassword(body: PasswordChangeBody): Promise<number> {
  return (await postJson<{ signed_out_sessions: number }>('/api/auth/password', body)).signed_out_sessions;
}

export function changeEmail(body: EmailChangeBody): Promise<void> {
  return postJson<void>('/api/auth/email', body);
}

export function createInvite(note: string | null): Promise<IssuedInvite> {
  return postJson<IssuedInvite>('/api/auth/invites', { note });
}

export async function listInvites(): Promise<InviteSummary[]> {
  return (await getJson<{ invites: InviteSummary[] }>('/api/auth/invites')).invites;
}

export function revokeInvite(id: string): Promise<void> {
  return postJson<void>(`/api/auth/invites/${encodeURIComponent(id)}/revoke`);
}

export async function inviteUsable(invite: string): Promise<boolean> {
  return (await getJson<{ usable: boolean }>(`/api/auth/invites/check?invite=${encodeURIComponent(invite)}`)).usable;
}

export async function logout(): Promise<void> {
  const response = await pipelineFetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
  if (!response.ok && response.status !== 404) throw await failure(response);
}

export async function listAuthSessions(): Promise<AuthSessionSummary[]> {
  return (await getJson<{ sessions: AuthSessionSummary[] }>('/api/auth/sessions')).sessions;
}

/** Signs out one other session, or every other one when no id is given; needs re-authentication. */
export async function revokeAuthSessions(body: RevokeSessionsBody): Promise<number> {
  return (await postJson<{ revoked: number }>('/api/auth/sessions/revoke', body)).revoked;
}

export function oidcLoginUrl(next: string): string {
  return `/api/auth/oidc/login?next=${encodeURIComponent(next)}`;
}

export function googleLoginUrl(next: string, options: { invite?: string | null; remember?: boolean } = {}): string {
  const query = new URLSearchParams({ next });
  if (options.invite) query.set('invite', options.invite);
  if (options.remember === false) query.set('remember', 'false');
  return `/api/auth/google/login?${query.toString()}`;
}

/** Starts connecting Google to the signed-in account; returns where the browser goes next. */
export async function startGoogleLink(next: string): Promise<string> {
  return (await postJson<{ url: string }>(`/api/auth/google/link?next=${encodeURIComponent(next)}`)).url;
}

export type DesktopGoogleStart = components['schemas']['DesktopGoogleStartResponse'];
export type DesktopGoogleHandoff = components['schemas']['DesktopGoogleHandoffResponse'];

/** Starts Google sign-in (or linking) for the desktop app; the nonce stays in this window (MarketPulse S-5). */
export function startDesktopGoogle(body: components['schemas']['DesktopGoogleStartRequest']): Promise<DesktopGoogleStart> {
  return postJson<DesktopGoogleStart>('/api/auth/google/desktop/start', body);
}

/** Trades the nonce for this window's session once Google answered; `pending` until then. */
export function takeDesktopGoogleHandoff(nonce: string): Promise<DesktopGoogleHandoff> {
  return postJson<DesktopGoogleHandoff>('/api/auth/google/desktop/handoff', { nonce });
}
