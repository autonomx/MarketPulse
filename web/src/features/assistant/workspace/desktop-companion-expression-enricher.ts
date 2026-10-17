import type { DesktopCompanionExpression } from './desktop-companion-delivery';
import { DESKTOP_COMPANION_DELIVERY_REQUEST_EVENT, DESKTOP_COMPANION_STATUS_EVENT } from '../../../events/bus';

let desktopCompanionExpressionEnricherInstalled = false;


type AttentionStatus = {
  observationId?: string | null;
  reaction?: string | null;
  rationale?: string | null;
};

type MutableDeliveryRequest = {
  observationId?: unknown;
  priority?: unknown;
  expression?: DesktopCompanionExpression;
  intensity?: number;
};

let latest: AttentionStatus = {};

export function expressionForDesktopAttention(status: AttentionStatus): {
  expression: DesktopCompanionExpression;
  intensity: number;
  critical: boolean;
} {
  const rationale = String(status.rationale || '');
  const critical = rationale.includes('high_importance') && rationale.includes('strong_visual_support');
  if (critical) return { expression: 'alert', intensity: 0.95, critical: true };
  if (status.reaction === 'deep') return { expression: 'focused', intensity: 0.75, critical: false };
  if (status.reaction === 'glance') return { expression: 'curious', intensity: 0.5, critical: false };
  return { expression: 'neutral', intensity: 0.25, critical: false };
}

export function initializeDesktopCompanionExpressionEnricher(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (desktopCompanionExpressionEnricherInstalled) return () => undefined;
  desktopCompanionExpressionEnricherInstalled = true;

  const handleStatus = (event: Event) => {
    latest = (event as CustomEvent<AttentionStatus>).detail ?? {};
  };
  const handleRequest = (event: Event) => {
    const request = (event as CustomEvent<MutableDeliveryRequest>).detail;
    if (!request || typeof request !== 'object') return;
    const observationId = typeof request.observationId === 'string' ? request.observationId : null;
    if (!observationId || latest.observationId !== observationId) return;
    const cue = expressionForDesktopAttention(latest);
    request.expression = cue.expression;
    request.intensity = cue.intensity;
    if (cue.critical) request.priority = 'critical';
  };

  window.addEventListener(DESKTOP_COMPANION_STATUS_EVENT, handleStatus);
  window.addEventListener(DESKTOP_COMPANION_DELIVERY_REQUEST_EVENT, handleRequest);
  return () => {
    window.removeEventListener(DESKTOP_COMPANION_STATUS_EVENT, handleStatus);
    window.removeEventListener(DESKTOP_COMPANION_DELIVERY_REQUEST_EVENT, handleRequest);
    latest = {};
    desktopCompanionExpressionEnricherInstalled = false;
  };
}
