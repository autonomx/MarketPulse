 
import { registerFetchMiddleware } from '../../../api/fetchPipeline';
export type LiveChatSubmissionInput = {
  sessionId: string;
  text: string;
  source: 'live_coordination';
  interrupted: boolean;
  segmentId: string;
  sourceSequence: number;
};

export type LiveChatSubmissionHandler = (input: LiveChatSubmissionInput) => Promise<void>;
export type LiveChatSubmissionFetchNext = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;
export type LiveChatSubmissionFetchInterceptor = (
  submission: LiveChatSubmissionInput,
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  next: LiveChatSubmissionFetchNext,
) => Promise<Response>;

type SubmissionFetchObserver = (
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  response: Promise<Response>,
) => void;

type ActiveSubmission = {
  completion: Promise<void>;
  abortController: AbortController;
  chatFetchObserved: boolean;
};

const CHAT_STREAM_PATH = /^\/api\/chat\/sessions\/([^/]+)\/messages\/stream$/;

export function liveSubmissionFetchMatches(
  submission: LiveChatSubmissionInput,
  input: RequestInfo | URL,
  init?: RequestInit,
): boolean {
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
  if (method !== 'POST') return false;
  const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
  const url = new URL(rawUrl, window.location.origin);
  const match = CHAT_STREAM_PATH.exec(url.pathname);
  return Boolean(match && decodeURIComponent(match[1]) === submission.sessionId);
}

export class LiveChatSubmissionGateway {
  private handler: LiveChatSubmissionHandler | null = null;
  private fetchInterceptor: LiveChatSubmissionFetchInterceptor | null = null;
  private activeSubmission: ActiveSubmission | null = null;

  register(handler: LiveChatSubmissionHandler): () => void {
    this.handler = handler;
    return () => {
      if (this.handler === handler) this.handler = null;
    };
  }

  registerFetchInterceptor(interceptor: LiveChatSubmissionFetchInterceptor): () => void {
    this.fetchInterceptor = interceptor;
    return () => {
      if (this.fetchInterceptor === interceptor) this.fetchInterceptor = null;
    };
  }

  async submit(input: LiveChatSubmissionInput): Promise<void> {
    const handler = this.handler;
    if (!handler) throw new Error('live_chat_submission_gateway_unavailable');

    await this.retireInterruptedSubmission(input);

    let settled = false;
    let submissionFetchObserved = false;
    let resolveAcceptance!: () => void;
    let rejectAcceptance!: (error: Error) => void;
    const acceptance = new Promise<void>((resolve, reject) => {
      resolveAcceptance = resolve;
      rejectAcceptance = reject;
    });

    const accept = (): void => {
      if (settled) return;
      settled = true;
      resolveAcceptance();
    };
    const reject = (error: unknown): void => {
      if (settled) return;
      settled = true;
      rejectAcceptance(error instanceof Error ? error : new Error(String(error)));
    };
    const observeFetch: SubmissionFetchObserver = (request, init, response) => {
      if (!liveSubmissionFetchMatches(input, request, init)) return;
      submissionFetchObserved = true;
      void response.then(
        (opened) => {
          if (opened.ok) accept();
          else reject(new Error(`live_chat_stream_status_${opened.status}`));
        },
        (error) => reject(error),
      );
    };

    const abortController = new AbortController();
    let completion: Promise<void>;
    try {
      completion = this.invokeHandler(input, handler, observeFetch, abortController);
    } catch (error) {
      completion = Promise.reject(error);
    }

    const activeSubmission: ActiveSubmission = {
      completion,
      abortController,
      chatFetchObserved: submissionFetchObserved,
    };
    this.activeSubmission = activeSubmission;

    void completion.then(
      () => {
        // Handlers without a chat-stream fetch retain the original completion
        // semantics. Once the exact submission fetch has been observed, only
        // that fetch may accept or reject this coordination attempt.
        if (!submissionFetchObserved) accept();
      },
      (error) => {
        if (!submissionFetchObserved) reject(error);
      },
    ).finally(() => {
      if (this.activeSubmission === activeSubmission) this.activeSubmission = null;
    });

    await acceptance;
  }

  private async retireInterruptedSubmission(input: LiveChatSubmissionInput): Promise<void> {
    if (!input.interrupted) return;
    const previous = this.activeSubmission;
    if (!previous?.chatFetchObserved) return;

    // The prior response may already be open while its workspace handler is
    // still consuming the body. Retire it before the replacement handler gets
    // ownership, so its abort/catch/finally cannot race the new voice turn.
    if (!previous.abortController.signal.aborted) previous.abortController.abort();
    try {
      await previous.completion;
    } catch {
      // The prior coordination was accepted when its response opened. This is
      // expected cleanup for the superseded body, not a replacement-turn error.
    }
    if (this.activeSubmission === previous) this.activeSubmission = null;
  }

  private invokeHandler(
    input: LiveChatSubmissionInput,
    handler: LiveChatSubmissionHandler,
    observeFetch: SubmissionFetchObserver,
    abortController: AbortController,
  ): Promise<void> {
    if (typeof window.fetch !== 'function') return Promise.resolve(handler(input));

    // The workspace handler starts its chat fetch synchronously before its first
    // await. Scope interception to that call only. The response promise is also
    // the submission identity used for early acceptance.
    const interceptor = this.fetchInterceptor;
    const remove = registerFetchMiddleware('live-chat-submission', (request, init, next) => {
      const existingSignal = init?.signal ?? (request instanceof Request ? request.signal : undefined);
      if (existingSignal && existingSignal !== abortController.signal) {
        if (existingSignal.aborted) abortController.abort();
        else existingSignal.addEventListener('abort', () => abortController.abort(), { once: true });
      }
      const scopedInit = { ...init, signal: abortController.signal };
      const response = interceptor
        ? interceptor(input, request, scopedInit, next as LiveChatSubmissionFetchNext)
        : next(request, scopedInit);
      observeFetch(request, scopedInit, response);
      return response;
    });
    try {
      return Promise.resolve(handler(input));
    } finally {
      remove();
    }
  }
}

export const liveChatSubmissionGateway = new LiveChatSubmissionGateway();
