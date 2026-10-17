// Run-scoped token for the Omnix broker and model gateway (WP-4.6).
//
// Omnix starts Pi with OMNIX_AGENT_RUN_TOKEN in its environment. The first
// extension to load moves it into a process-global slot and deletes the
// variable, so shell and other tool child processes never inherit it. The
// holder renews the token through the broker before it expires.

const SLOT = Symbol.for("omnix.agent.runToken");
const RENEW_BEFORE_MS = 5 * 60 * 1000;
const RENEW_CHECK_MS = 60 * 1000;

type RunTokenState = {
  token: string;
  expiresAtMs: number;
  renewal: Promise<void> | null;
  timer: ReturnType<typeof setInterval> | null;
  listeners: Set<(token: string) => void>;
};

function expiryOf(token: string): number {
  try {
    const payload = token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
    const claims = JSON.parse(Buffer.from(payload, "base64").toString("utf8"));
    return Number(claims.exp) * 1000 || 0;
  } catch {
    return 0;
  }
}

export function runTokenState(): RunTokenState {
  const holder = globalThis as unknown as Record<symbol, RunTokenState | undefined>;
  let state = holder[SLOT];
  if (!state) {
    const token = process.env.OMNIX_AGENT_RUN_TOKEN || "";
    delete process.env.OMNIX_AGENT_RUN_TOKEN;
    state = { token, expiresAtMs: expiryOf(token), renewal: null, timer: null, listeners: new Set() };
    holder[SLOT] = state;
  }
  return state;
}

export function onRunTokenRenewed(listener: (token: string) => void): void {
  runTokenState().listeners.add(listener);
}

async function renew(state: RunTokenState, brokerUrl: string, runId: string): Promise<void> {
  const response = await fetch(`${brokerUrl}/${encodeURIComponent(runId)}/run-token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Omnix-Client": "agent-runtime",
      Authorization: `OmnixRun ${state.token}`,
    },
    body: "{}",
  });
  if (!response.ok) return; // Keep the current token; the server decides when it is void.
  const payload: any = await response.json();
  if (typeof payload?.token !== "string" || !payload.token) return;
  state.token = payload.token;
  state.expiresAtMs = Number(payload.expires_at) * 1000 || expiryOf(payload.token);
  for (const listener of state.listeners) listener(state.token);
}

export async function runAuthorization(brokerUrl: string, runId: string): Promise<string> {
  const state = runTokenState();
  if (state.token && state.expiresAtMs - Date.now() < RENEW_BEFORE_MS) {
    state.renewal ??= renew(state, brokerUrl, runId)
      .catch(() => undefined)
      .finally(() => {
        state.renewal = null;
      });
    await state.renewal;
  }
  return `OmnixRun ${state.token}`;
}

// Model calls do not pass through runAuthorization, so keep renewing in the
// background for as long as the process lives.
export function keepRunTokenFresh(brokerUrl: string, runId: string): void {
  const state = runTokenState();
  if (state.timer || !state.token) return;
  state.timer = setInterval(() => {
    void runAuthorization(brokerUrl, runId);
  }, RENEW_CHECK_MS);
  (state.timer as any).unref?.();
}
