import { useEffect, useState } from 'react';
import { type DesktopCompanionExpression } from './desktop-companion-delivery';
import type { LiveConversationState } from './live-conversation-state';
import { useLiveConversationState } from './live-conversation-store';
import type { SpeechDeliveryPlan } from './live-speech-delivery-plan';
import { DESKTOP_COMPANION_EXPRESSION_EVENT } from '../../../events/bus';

export type AvatarPresenceCue = 'idle' | 'listening' | 'thinking' | 'speaking' | 'yielding' | 'restrained';

type CompanionExpressionDetail = {
  active?: boolean;
  expression?: DesktopCompanionExpression;
  intensity?: number;
};

export function deriveAvatarPresenceCue(
  state: LiveConversationState,
  plan: SpeechDeliveryPlan | null = null,
): AvatarPresenceCue {
  if (state.connection === 'disconnected') return 'idle';
  if (state.bargeIn === 'accepted' || state.assistantTurn === 'interrupted') return 'yielding';
  if (plan && plan.energy === 'low' && plan.warmth === 'high') return 'restrained';
  if (state.userTurn === 'speaking' || state.userTurn === 'speech_candidate' || state.floorOwner === 'user') return 'listening';
  if (state.assistantTurn === 'planning' || state.assistantTurn === 'generating') return 'thinking';
  if (state.assistantTurn === 'speaking' || state.floorOwner === 'assistant') return 'speaking';
  return 'idle';
}

export type AvatarPresence = {
  cue: AvatarPresenceCue;
  expression: DesktopCompanionExpression;
  intensity: number;
};

/** The avatar's presence cue and the desktop companion's expression, for the call orb. */
export function useAvatarPresence(): AvatarPresence {
  const runtime = useLiveConversationState();
  const [expression, setExpression] = useState<{ expression: DesktopCompanionExpression; intensity: number }>({ expression: 'neutral', intensity: 0 });

  useEffect(() => {
    const handleExpression = (event: Event) => {
      const detail = (event as CustomEvent<CompanionExpressionDetail>).detail ?? {};
      if (detail.active === false) {
        setExpression({ expression: 'neutral', intensity: 0 });
      } else if (detail.expression && ['neutral', 'curious', 'focused', 'alert'].includes(detail.expression)) {
        const intensity = typeof detail.intensity === 'number' && Number.isFinite(detail.intensity) ? detail.intensity : 0.5;
        setExpression({ expression: detail.expression, intensity: Math.max(0, Math.min(1, intensity)) });
      }
    };
    window.addEventListener(DESKTOP_COMPANION_EXPRESSION_EVENT, handleExpression);
    return () => window.removeEventListener(DESKTOP_COMPANION_EXPRESSION_EVENT, handleExpression);
  }, []);

  return { cue: deriveAvatarPresenceCue(runtime.conversation, runtime.deliveryPlan), ...expression };
}
