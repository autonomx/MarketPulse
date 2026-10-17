const SELECTOR = '.assistant-message-input textarea';
const pendingValues = new WeakMap<HTMLTextAreaElement, string>();
let redispatching = false;
let initialized = false;

export function captureLiveVoiceInput(event: Event): void {
  const target = event.target;
  if (redispatching || event.isTrusted || !(target instanceof HTMLTextAreaElement)) return;
  if (!target.matches(SELECTOR)) return;

  pendingValues.set(target, target.value);
  target.setRangeText('', 0, target.value.length, 'end');
}

export function finishLiveVoiceInput(event: Event): void {
  const target = event.target;
  if (redispatching || !(target instanceof HTMLTextAreaElement)) return;
  const value = pendingValues.get(target);
  if (value === undefined) return;

  pendingValues.delete(target);
  redispatching = true;
  target.setRangeText(value, 0, target.value.length, 'end');
  target.dispatchEvent(new Event('input', { bubbles: true }));
  redispatching = false;
}

export function initializeLiveVoiceFormSync(root: Document = document): () => void {
  if (initialized) return () => undefined;
  initialized = true;
  root.addEventListener('input', captureLiveVoiceInput, true);
  root.addEventListener('input', finishLiveVoiceInput);
  return () => {
    root.removeEventListener('input', captureLiveVoiceInput, true);
    root.removeEventListener('input', finishLiveVoiceInput);
    initialized = false;
  };
}
