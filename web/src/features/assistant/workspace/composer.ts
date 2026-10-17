export type ComposerControl = 'provider' | 'model' | 'identity' | 'workspace' | 'project' | 'memory' | 'library' | 'tools' | 'voice';

export type ComposerState = {
  draft: string;
  controls: ComposerControl[];
  submitEnabled: boolean;
};

export const DEFAULT_COMPOSER_CONTROLS: ComposerControl[] = [
  'provider',
  'model',
  'identity',
  'workspace',
  'project',
  'memory',
  'library',
  'tools',
  'voice',
];

export function createComposerState(draft = '', controls = DEFAULT_COMPOSER_CONTROLS): ComposerState {
  return {
    draft,
    controls: [...controls],
    submitEnabled: draft.trim().length > 0,
  };
}

export function toggleComposerControl(state: ComposerState, control: ComposerControl): ComposerState {
  const controls = state.controls.includes(control)
    ? state.controls.filter((item) => item !== control)
    : [...state.controls, control];
  return { ...state, controls };
}
