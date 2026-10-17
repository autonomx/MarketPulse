import { describe, expect, it } from 'vitest';
import {
  createAssistantIdentity,
  isDefaultAssistantIdentityName,
  updateAssistantIdentityPrompt,
} from './identity';

const identity = {
  id: 'identity-1',
  name: 'Architect',
  description: 'Architecture helper',
  systemPrompt: 'Think structurally.',
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
};

describe('assistant identity contracts', () => {
  it('recognizes default identity names', () => {
    expect(isDefaultAssistantIdentityName('Architect')).toBe(true);
    expect(isDefaultAssistantIdentityName('Unknown')).toBe(false);
  });

  it('updates prompts immutably', () => {
    const created = createAssistantIdentity(identity);
    const updated = updateAssistantIdentityPrompt(created, 'New prompt.', '2026-01-02T00:00:00.000Z');

    expect(updated.systemPrompt).toBe('New prompt.');
    expect(updated.updatedAt).toBe('2026-01-02T00:00:00.000Z');
    expect(created.systemPrompt).toBe('Think structurally.');
  });
});
