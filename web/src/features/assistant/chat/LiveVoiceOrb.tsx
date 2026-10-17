import type { CSSProperties } from 'react';
import { useAvatarPresence } from '../workspace';

const METER_BARS = [0, 1, 2, 3, 4, 5, 6] as const;

/**
 * The live call orb. It reads the avatar presence itself, so live
 * conversation updates re-render the orb rather than all of Chat.
 */
export function LiveVoiceOrb({ voiceMode }: { voiceMode: string }) {
  const presence = useAvatarPresence();
  return (
    <div
      className="assistant-voice-orb"
      data-voice-mode={voiceMode}
      data-presence-cue={presence.cue}
      data-companion-expression={presence.expression}
      data-companion-intensity={presence.intensity.toFixed(2)}
      aria-hidden="true"
    >
      <div className="assistant-voice-meter assistant-voice-meter-left">{METER_BARS.map((index) => <i key={`left-${index}`} style={{ '--bar-index': index } as CSSProperties} />)}</div>
      <div className="assistant-voice-core"><span className="assistant-voice-pulse" /><span className="assistant-voice-mic" /></div>
      <div className="assistant-voice-meter assistant-voice-meter-right">{METER_BARS.map((index) => <i key={`right-${index}`} style={{ '--bar-index': index } as CSSProperties} />)}</div>
    </div>
  );
}
