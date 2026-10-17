import { DEFAULT_SETTINGS_DOCUMENT } from '../settings';
import { rpgCampaignDefaults } from '../settings';
import type { SettingsDocument } from '../settings';

export type RpgWizardSystemDefaults = {
  autosave: boolean;
  companions: boolean;
  permadeath: boolean;
  grounding: boolean;
  softAudit: boolean;
  narration: boolean;
  images: boolean;
  tts: boolean;
  stt: boolean;
};

export type RpgWizardDefaults = {
  difficulty: 'story' | 'normal' | 'hard';
  worldActivity: 'quiet' | 'standard' | 'busy';
  economyPressure: 'low' | 'normal' | 'tight';
  combatLethality: 'forgiving' | 'normal' | 'deadly';
  systems: RpgWizardSystemDefaults;
};

const SYSTEM_LABELS: Record<keyof RpgWizardSystemDefaults, string> = {
  autosave: 'Autosave',
  companions: 'Companions enabled',
  permadeath: 'Permadeath',
  grounding: 'Grounding validator',
  softAudit: 'Background soft audit',
  narration: 'LLM narration',
  images: 'Image generation',
  tts: 'TTS',
  stt: 'STT',
};

export function rpgWizardDefaultsFromSettings(
  document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT,
): RpgWizardDefaults {
  const value = rpgCampaignDefaults(document);
  return {
    difficulty: value.difficulty === 'harsh' ? 'hard' : value.difficulty,
    worldActivity: value.worldActivity === 'living_world' ? 'busy' : value.worldActivity,
    economyPressure: value.economyPressure === 'relaxed' ? 'low' : value.economyPressure === 'strict' ? 'tight' : 'normal',
    combatLethality: value.combatLethality === 'safe' ? 'forgiving' : value.combatLethality,
    systems: {
      autosave: value.autosave,
      companions: value.companions,
      permadeath: value.permadeath,
      grounding: value.validator,
      softAudit: value.backgroundSoftAudit,
      narration: value.llmNarration,
      images: value.imageGeneration,
      tts: value.tts,
      stt: value.stt,
    },
  };
}

export function applyRpgWizardDefaults(root: HTMLElement, defaults: RpgWizardDefaults): number {
  let applied = 0;
  applied += setLabeledSelect(root, 'Difficulty', defaults.difficulty);
  applied += setLabeledSelect(root, 'World activity', defaults.worldActivity);
  applied += setLabeledSelect(root, 'Economy pressure', defaults.economyPressure);
  applied += setLabeledSelect(root, 'Combat lethality', defaults.combatLethality);

  (Object.keys(SYSTEM_LABELS) as Array<keyof RpgWizardSystemDefaults>).forEach((key) => {
    const checkbox = findLabeledControl(root, SYSTEM_LABELS[key], 'input[type="checkbox"]');
    if (!(checkbox instanceof HTMLInputElement) || checkbox.checked === defaults.systems[key]) return;
    checkbox.click();
    applied += 1;
  });

  return applied;
}

function setLabeledSelect(root: HTMLElement, label: string, value: string): number {
  const select = findLabeledControl(root, label, 'select');
  if (!(select instanceof HTMLSelectElement) || select.value === value) return 0;
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
  setter?.call(select, value);
  select.dispatchEvent(new Event('change', { bubbles: true }));
  return 1;
}

function findLabeledControl(root: HTMLElement, label: string, selector: string): Element | null {
  const labels = Array.from(root.querySelectorAll('label'));
  const match = labels.find((candidate) => {
    const text = Array.from(candidate.children).find((child) => child.tagName === 'SPAN')?.textContent?.trim();
    return text === label;
  });
  return match?.querySelector(selector) ?? null;
}
