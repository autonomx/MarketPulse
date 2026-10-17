export type DesktopCompanionPhase =
  | 'off'
  | 'sharing'
  | 'watching_idle'
  | 'change_pending'
  | 'analyzing'
  | 'observation_ready'
  | 'paused'
  | 'backing_off'
  | 'error';

export type DesktopCompanionBinding = {
  sessionId: string;
  characterId: string | null;
  sourceFingerprint: string;
  captureGeneration: string;
};

export type DesktopCompanionPreflight = {
  ready: boolean;
  modelId: string | null;
  endpoint: string | null;
  remote: boolean;
  reason: string;
};

export type DesktopCompanionSnapshot = {
  phase: DesktopCompanionPhase;
  binding: DesktopCompanionBinding | null;
  watchEnabled: boolean;
  speechMuted: boolean;
  shadowMode: boolean;
  pageVisible: boolean;
  clientSequence: number;
  lastError: string | null;
  preflight: DesktopCompanionPreflight | null;
};

export type DesktopCompanionRuntimeOptions = {
  createGeneration?: () => string;
  pageVisible?: () => boolean;
};

type Listener = (snapshot: DesktopCompanionSnapshot) => void;

const DEFAULT_PREFLIGHT: DesktopCompanionPreflight = {
  ready: false,
  modelId: null,
  endpoint: null,
  remote: false,
  reason: 'vision_preflight_missing',
};

export class DesktopCompanionRuntime {
  private readonly createGeneration: () => string;
  private readonly pageVisible: () => boolean;
  private readonly listeners = new Set<Listener>();
  private snapshotValue: DesktopCompanionSnapshot;

  constructor(options: DesktopCompanionRuntimeOptions = {}) {
    this.createGeneration = options.createGeneration ?? (() => crypto.randomUUID());
    this.pageVisible = options.pageVisible ?? (() => typeof document === 'undefined' || document.visibilityState === 'visible');
    this.snapshotValue = {
      phase: 'off',
      binding: null,
      watchEnabled: false,
      speechMuted: false,
      shadowMode: true,
      pageVisible: this.pageVisible(),
      clientSequence: 0,
      lastError: null,
      preflight: null,
    };
  }

  getSnapshot(): DesktopCompanionSnapshot {
    return this.snapshotValue;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  beginSharing(input: { sessionId: string; characterId?: string | null; sourceFingerprint: string }): DesktopCompanionBinding {
    const sessionId = input.sessionId.trim();
    const sourceFingerprint = input.sourceFingerprint.trim();
    if (!sessionId) throw new Error('desktop companion requires a session binding');
    if (!sourceFingerprint) throw new Error('desktop companion requires a capture source fingerprint');
    const binding: DesktopCompanionBinding = {
      sessionId,
      characterId: input.characterId?.trim() || null,
      sourceFingerprint,
      captureGeneration: this.createGeneration(),
    };
    this.publish({
      phase: 'sharing',
      binding,
      watchEnabled: false,
      clientSequence: 0,
      pageVisible: this.pageVisible(),
      lastError: null,
    });
    return binding;
  }

  rebindSession(sessionId: string, characterId: string | null = null): void {
    const current = this.snapshotValue.binding;
    const normalized = sessionId.trim();
    if (!current || !normalized) {
      this.stopAndForget();
      return;
    }
    if (current.sessionId === normalized && current.characterId === characterId) return;
    this.publish({
      phase: 'sharing',
      binding: {
        ...current,
        sessionId: normalized,
        characterId,
        captureGeneration: this.createGeneration(),
      },
      watchEnabled: false,
      clientSequence: 0,
      lastError: null,
    });
  }

  setPreflight(preflight: DesktopCompanionPreflight): void {
    this.publish({ preflight: { ...DEFAULT_PREFLIGHT, ...preflight } });
    if (!preflight.ready && this.snapshotValue.watchEnabled) this.pause('vision_preflight_failed');
  }

  enableWatch(options: { shadowMode?: boolean; speechMuted?: boolean } = {}): void {
    if (!this.snapshotValue.binding) throw new Error('screen sharing must be active before Companion Watch');
    const preflight = this.snapshotValue.preflight ?? DEFAULT_PREFLIGHT;
    if (!preflight.ready) throw new Error(preflight.reason || 'vision preflight failed');
    const visible = this.pageVisible();
    this.publish({
      phase: visible ? 'watching_idle' : 'paused',
      watchEnabled: true,
      shadowMode: options.shadowMode ?? this.snapshotValue.shadowMode,
      speechMuted: options.speechMuted ?? this.snapshotValue.speechMuted,
      pageVisible: visible,
      lastError: visible ? null : 'page_hidden',
    });
  }

  pause(reason = 'paused_by_user'): void {
    if (!this.snapshotValue.binding) return;
    this.publish({ phase: 'paused', watchEnabled: false, lastError: reason });
  }

  resume(): void {
    if (!this.snapshotValue.binding) throw new Error('screen sharing is not active');
    const preflight = this.snapshotValue.preflight ?? DEFAULT_PREFLIGHT;
    if (!preflight.ready) throw new Error(preflight.reason || 'vision preflight failed');
    const visible = this.pageVisible();
    this.publish({
      phase: visible ? 'watching_idle' : 'paused',
      watchEnabled: visible,
      pageVisible: visible,
      lastError: visible ? null : 'page_hidden',
    });
  }

  setSpeechMuted(value: boolean): void {
    this.publish({ speechMuted: value });
  }

  handleVisibility(visible = this.pageVisible()): void {
    this.publish({ pageVisible: visible });
    if (!visible && this.snapshotValue.watchEnabled) this.pause('page_hidden');
  }

  markPhase(phase: DesktopCompanionPhase, error: string | null = null): void {
    if (!this.snapshotValue.binding && phase !== 'off') return;
    this.publish({ phase, lastError: error });
  }

  nextSequence(): { binding: DesktopCompanionBinding; clientSequence: number } {
    const binding = this.snapshotValue.binding;
    if (!binding) throw new Error('desktop companion capture is not bound');
    const clientSequence = this.snapshotValue.clientSequence + 1;
    this.publish({ clientSequence });
    return { binding, clientSequence };
  }

  acceptsResult(input: { captureGeneration: string; clientSequence: number }): boolean {
    const binding = this.snapshotValue.binding;
    if (!binding) return false;
    return input.captureGeneration === binding.captureGeneration
      && input.clientSequence === this.snapshotValue.clientSequence
      && this.snapshotValue.phase !== 'off'
      && this.snapshotValue.phase !== 'paused';
  }

  stopAndForget(): void {
    this.publish({
      phase: 'off',
      binding: null,
      watchEnabled: false,
      clientSequence: 0,
      lastError: null,
      preflight: null,
    });
  }

  private publish(patch: Partial<DesktopCompanionSnapshot>): void {
    this.snapshotValue = { ...this.snapshotValue, ...patch };
    for (const listener of this.listeners) listener(this.snapshotValue);
  }
}
