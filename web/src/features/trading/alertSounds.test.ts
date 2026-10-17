import { afterEach, describe, expect, it, vi } from 'vitest';
import { alertSoundName, playAlertSound, resetAlertSounds, soundChange } from './alertSounds';

function fakeAudio() {
  const started: number[] = [];
  const param = () => ({ value: 0, setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() });
  return {
    started,
    audio: {
      currentTime: 10,
      destination: {},
      state: 'running',
      resume: vi.fn(async () => undefined),
      createOscillator: () => ({ frequency: param(), connect: vi.fn(), start: (at: number) => started.push(at), stop: vi.fn() }),
      createGain: () => ({ gain: param(), connect: vi.fn() }),
    },
  };
}

afterEach(() => resetAlertSounds());

describe('alert sounds', () => {
  it('plays the chosen sound, the chime by default', () => {
    const { audio, started } = fakeAudio();
    expect(playAlertSound(undefined, () => audio as never)).toBe(true);
    expect(started).toEqual([10, 10.16]);
    expect(alertSoundName('alarm')).toBe('alarm');
    expect(alertSoundName('siren')).toBe('chime');
  });

  it('reports a browser without audio instead of throwing', () => {
    expect(playAlertSound('beep', () => null)).toBe(false);
    resetAlertSounds();
    expect(playAlertSound('beep', () => { throw new Error('blocked'); })).toBe(false);
  });
});

describe('alert sounds before a user gesture', () => {
  it('plays nothing while the browser keeps audio suspended, and asks to resume', () => {
    const { audio, started } = fakeAudio();
    const suspended = { ...audio, state: 'suspended' };
    expect(playAlertSound('chime', () => suspended as never)).toBe(false);
    expect(started).toEqual([]);
    expect(suspended.resume).toHaveBeenCalled();
  });
});

describe('soundChange', () => {
  it('saves the sound only with the Sound channel and only when it changed', () => {
    expect(soundChange({ notifications: ['app'], sound: 'beep' })).toBeUndefined();
    expect(soundChange({ notifications: ['sound'] })).toBe('chime');
    expect(soundChange({ notifications: ['sound'], sound: 'chime', storedSound: 'siren' })).toBeUndefined();
    expect(soundChange({ notifications: ['sound'], sound: 'beep', storedSound: 'siren' })).toBe('beep');
    expect(soundChange({ notifications: ['sound'], sound: 'alarm', storedSound: 'alarm' })).toBeUndefined();
  });
});
