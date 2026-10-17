/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { describe, expect, it } from 'vitest';
import { DEFAULT_SETTINGS_DOCUMENT } from '../settings';
import { applyRpgWizardDefaults, rpgWizardDefaultsFromSettings } from './rpgWizardDefaults';

describe('RPG wizard defaults', () => {
  it('maps central settings into the wizard option vocabulary', () => {
    const document = {
      ...DEFAULT_SETTINGS_DOCUMENT,
      rpg: {
        ...DEFAULT_SETTINGS_DOCUMENT.rpg,
        difficulty: 'harsh' as const,
        worldActivity: 'living_world' as const,
        economyPressure: 'strict' as const,
        combatLethality: 'safe' as const,
        autosave: false,
        companions: false,
        permadeath: true,
        validator: false,
        backgroundSoftAudit: false,
        llmNarration: false,
        imageGeneration: true,
        tts: false,
        stt: false,
      },
    };

    expect(rpgWizardDefaultsFromSettings(document)).toEqual({
      difficulty: 'hard',
      worldActivity: 'busy',
      economyPressure: 'tight',
      combatLethality: 'forgiving',
      systems: {
        autosave: false,
        companions: false,
        permadeath: true,
        grounding: false,
        softAudit: false,
        narration: false,
        images: true,
        tts: false,
        stt: false,
      },
    });
  });

  it('applies mapped defaults through the wizard controls', () => {
    const root = document.createElement('div');
    root.innerHTML = `
      <label><span>Difficulty</span><select><option>normal</option><option>hard</option></select></label>
      <label><span>World activity</span><select><option>standard</option><option>busy</option></select></label>
      <label><span>Economy pressure</span><select><option>normal</option><option>tight</option></select></label>
      <label><span>Combat lethality</span><select><option>normal</option><option>forgiving</option></select></label>
      <label><input type="checkbox" checked><span>Autosave</span></label>
      <label><input type="checkbox" checked><span>Companions enabled</span></label>
      <label><input type="checkbox"><span>Permadeath</span></label>
      <label><input type="checkbox" checked><span>Grounding validator</span></label>
      <label><input type="checkbox" checked><span>Background soft audit</span></label>
      <label><input type="checkbox" checked><span>LLM narration</span></label>
      <label><input type="checkbox"><span>Image generation</span></label>
      <label><input type="checkbox" checked><span>TTS</span></label>
      <label><input type="checkbox" checked><span>STT</span></label>
    `;

    const applied = applyRpgWizardDefaults(root, {
      difficulty: 'hard',
      worldActivity: 'busy',
      economyPressure: 'tight',
      combatLethality: 'forgiving',
      systems: {
        autosave: false,
        companions: false,
        permadeath: true,
        grounding: false,
        softAudit: false,
        narration: false,
        images: true,
        tts: false,
        stt: false,
      },
    });

    expect(applied).toBe(13);
    expect(Array.from(root.querySelectorAll('select')).map((select) => select.value)).toEqual([
      'hard',
      'busy',
      'tight',
      'forgiving',
    ]);
    expect(Array.from(root.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).map((input) => input.checked)).toEqual([
      false,
      false,
      true,
      false,
      false,
      false,
      true,
      false,
      false,
    ]);
  });
});
