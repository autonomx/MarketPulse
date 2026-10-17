export type CompanionInitiativeSource = 'desktop' | 'social' | 'ambient' | 'memory';
export type CompanionInitiativePriority = 'normal' | 'critical';

export type CompanionInitiativeRequest = {
  source: CompanionInitiativeSource;
  priority?: CompanionInitiativePriority;
  nowMs: number;
  cooldownMs: number;
};

export type CompanionInitiativeDecision = {
  accepted: boolean;
  reason: 'initiative_acquired' | 'initiative_active' | 'initiative_cooldown';
  token: string | null;
  eligibleInMs: number;
};

export type CompanionInitiativeSnapshot = {
  activeSource: CompanionInitiativeSource | null;
  activeToken: string | null;
  activeSinceMs: number | null;
  lastDeliveredAtMs: number | null;
  lastDeliveredSource: CompanionInitiativeSource | null;
};

type ActiveInitiative = {
  source: CompanionInitiativeSource;
  token: string;
  startedAtMs: number;
};

export class CompanionInitiativeArbiter {
  private active: ActiveInitiative | null = null;
  private lastDeliveredAtMs: number | null = null;
  private lastDeliveredSource: CompanionInitiativeSource | null = null;
  private sequence = 0;

  begin(request: CompanionInitiativeRequest): CompanionInitiativeDecision {
    const nowMs = finiteNow(request.nowMs);
    if (this.active) {
      return {
        accepted: false,
        reason: 'initiative_active',
        token: null,
        eligibleInMs: 100,
      };
    }

    const priority = request.priority ?? 'normal';
    const cooldownMs = priority === 'critical' ? 0 : Math.max(0, request.cooldownMs);
    if (this.lastDeliveredAtMs !== null) {
      const remaining = Math.max(0, cooldownMs - (nowMs - this.lastDeliveredAtMs));
      if (remaining > 0) {
        return {
          accepted: false,
          reason: 'initiative_cooldown',
          token: null,
          eligibleInMs: remaining,
        };
      }
    }

    this.sequence += 1;
    const token = `companion-initiative:${this.sequence}:${Math.round(nowMs)}`;
    this.active = { source: request.source, token, startedAtMs: nowMs };
    return {
      accepted: true,
      reason: 'initiative_acquired',
      token,
      eligibleInMs: 0,
    };
  }

  finish(token: string | null | undefined, nowMs: number, delivered: boolean): boolean {
    if (!token || !this.active || this.active.token !== token) return false;
    const source = this.active.source;
    this.active = null;
    if (delivered) {
      this.lastDeliveredAtMs = finiteNow(nowMs);
      this.lastDeliveredSource = source;
    }
    return true;
  }

  snapshot(): CompanionInitiativeSnapshot {
    return {
      activeSource: this.active?.source ?? null,
      activeToken: this.active?.token ?? null,
      activeSinceMs: this.active?.startedAtMs ?? null,
      lastDeliveredAtMs: this.lastDeliveredAtMs,
      lastDeliveredSource: this.lastDeliveredSource,
    };
  }

  reset(): void {
    this.active = null;
    this.lastDeliveredAtMs = null;
    this.lastDeliveredSource = null;
    this.sequence = 0;
  }
}

function finiteNow(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

export const companionInitiativeArbiter = new CompanionInitiativeArbiter();
