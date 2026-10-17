/**
 * Browser error reports to /api/client-errors (WP-9.9).
 *
 * Reports carry the message, a trimmed stack, the route path (never the query
 * string), the workspace and the build, plus the request id of a failed API
 * call when the error is an ApiError. At most 10 reports a minute, and an
 * identical report only once every 30 seconds.
 */
import { activeViewModule } from './viewApiScope';
import { api } from '../api/http';

export type ClientErrorKind = 'error' | 'unhandledrejection' | 'render' | 'chunk_load';

const MAX_PER_MINUTE = 10;
const REPEAT_WINDOW_MS = 30_000;

const sentAt: number[] = [];
const recent = new Map<string, number>();
let installed = false;

function buildId(): string | undefined {
  const value = (import.meta as ImportMeta & { env?: Record<string, unknown> }).env?.VITE_GIT_SHA;
  return typeof value === 'string' && value ? value.slice(0, 64) : undefined;
}

function describe(error: unknown): { message: string; stack?: string; apiRequestId?: string } {
  if (error instanceof Error) {
    const requestId = (error as Error & { requestId?: unknown }).requestId;
    return {
      message: `${error.name}: ${error.message}`.slice(0, 500),
      stack: error.stack?.slice(0, 4000),
      apiRequestId: typeof requestId === 'string' ? requestId.slice(0, 128) : undefined,
    };
  }
  return { message: String(error ?? 'Unknown error').slice(0, 500) };
}

function allowed(signature: string, now: number): boolean {
  while (sentAt.length && now - sentAt[0] > 60_000) sentAt.shift();
  const last = recent.get(signature);
  if (last !== undefined && now - last < REPEAT_WINDOW_MS) return false;
  if (sentAt.length >= MAX_PER_MINUTE) return false;
  sentAt.push(now);
  recent.set(signature, now);
  if (recent.size > 100) recent.delete(recent.keys().next().value as string);
  return true;
}

/** Send one report; never throws and never reports its own failure. */
export function reportClientError(kind: ClientErrorKind, error: unknown): void {
  if (typeof window === 'undefined') return;
  const { message, stack, apiRequestId } = describe(error);
  if (!allowed(`${kind}:${message}`, Date.now())) return;
  const body = {
    kind,
    message,
    stack,
    route: window.location.pathname || '/',
    module: activeViewModule(),
    build: buildId(),
    api_request_id: apiRequestId,
  };
  void api.POST('/api/client-errors', { body, keepalive: true }).catch(() => undefined);
}

/** Report uncaught errors and unhandled rejections; returns a function that stops it. */
export function installClientErrorReporting(): () => void {
  if (installed || typeof window === 'undefined') return () => undefined;
  installed = true;
  const handleError = (event: ErrorEvent) => reportClientError('error', event.error ?? event.message);
  const handleRejection = (event: PromiseRejectionEvent) => reportClientError('unhandledrejection', event.reason);
  window.addEventListener('error', handleError);
  window.addEventListener('unhandledrejection', handleRejection);
  return () => {
    window.removeEventListener('error', handleError);
    window.removeEventListener('unhandledrejection', handleRejection);
    installed = false;
  };
}

export function resetClientErrorReportingForTests(): void {
  sentAt.length = 0;
  recent.clear();
}
