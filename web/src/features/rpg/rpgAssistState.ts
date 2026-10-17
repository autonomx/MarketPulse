export type RpgAssistState = 'idle' | 'loading' | 'ready' | 'error' | 'empty';

export interface RpgAssistItemPreview {
  id?: string;
  label?: string;
  command?: string;
  kind?: string;
  reason?: string;
}

export function rpgAssistStateFromItems(items: RpgAssistItemPreview[] | undefined, pending: boolean, failed: boolean): RpgAssistState {
  if (pending) return 'loading';
  if (failed) return 'error';
  return items?.length ? 'ready' : 'empty';
}
