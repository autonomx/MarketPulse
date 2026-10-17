// Alert sounds (the alert "Sound" channel): short tones synthesized with the
// Web Audio API, so no audio files ship. Browsers may refuse to play before the
// page had a user gesture; that is reported, not thrown.

export type AlertSoundName = 'chime' | 'beep' | 'alarm';
export const ALERT_SOUNDS: readonly AlertSoundName[] = ['chime', 'beep', 'alarm'];

/** Each sound as notes: frequency (Hz), start and length (seconds). */
const NOTES: Record<AlertSoundName, readonly { frequency: number; at: number; length: number }[]> = {
  chime: [{ frequency: 880, at: 0, length: 0.18 }, { frequency: 1320, at: 0.16, length: 0.32 }],
  beep: [{ frequency: 1000, at: 0, length: 0.15 }],
  alarm: [0, 0.22, 0.44].flatMap((at) => [{ frequency: 960, at, length: 0.1 }, { frequency: 720, at: at + 0.1, length: 0.1 }]),
};

export function alertSoundName(value: unknown): AlertSoundName {
  return ALERT_SOUNDS.includes(value as AlertSoundName) ? (value as AlertSoundName) : 'chime';
}

type AudioContextLike = Pick<AudioContext, 'currentTime' | 'destination' | 'createOscillator' | 'createGain' | 'state' | 'resume'>;

let shared: AudioContextLike | null = null;

function context(create: () => AudioContextLike | null): AudioContextLike | null {
  if (!shared) shared = create();
  return shared;
}

function defaultContext(): AudioContextLike | null {
  const Constructor = typeof window !== 'undefined'
    ? (window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)
    : undefined;
  return Constructor ? new Constructor() : null;
}

/** Plays an alert sound; false when the browser has no audio or refused it. */
export function playAlertSound(name: unknown, create: () => AudioContextLike | null = defaultContext): boolean {
  try {
    const audio = context(create);
    if (!audio) return false;
    if (audio.state !== 'running') {
      // Without a user gesture yet the browser keeps it suspended; ask to resume for the next sound, play nothing
      // now (scheduled notes would wait and play together once a click resumes it).
      void audio.resume().catch(() => undefined);
      return false;
    }
    const start = audio.currentTime;
    for (const note of NOTES[alertSoundName(name)]) {
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      oscillator.frequency.value = note.frequency;
      gain.gain.setValueAtTime(0.0001, start + note.at);
      gain.gain.exponentialRampToValueAtTime(0.25, start + note.at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + note.at + note.length);
      oscillator.connect(gain);
      gain.connect(audio.destination);
      oscillator.start(start + note.at);
      oscillator.stop(start + note.at + note.length + 0.02);
    }
    return true;
  } catch {
    return false;
  }
}

/** The sound to save: only with the Sound channel, and only when it differs from the stored one (kept if unknown here). */
export function soundChange(editor: { notifications: readonly string[]; sound?: AlertSoundName; storedSound?: string }): string | undefined {
  if (!editor.notifications.includes('sound')) return undefined;
  const chosen = editor.sound ?? 'chime';
  if (editor.storedSound === undefined) return chosen;
  return alertSoundName(editor.storedSound) === chosen ? undefined : chosen;
}

/** Tests only: forget the audio context. */
export function resetAlertSounds(): void {
  shared = null;
}
