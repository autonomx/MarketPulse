import { describe, expect, it } from 'vitest';

import {
  desktopStatusLabel,
  localWorkspaceSummary,
  normalizeDeepResearchPageLimit,
  normalizeLocalWorkspaceSelection,
  normalizeResearchMode,
  webResearchModeLabel,
} from './assistant-context-controller';

describe('assistant context helpers', () => {
  it('keeps non-sharing desktop status messages visible', () => {
    expect(desktopStatusLabel(false, 'Off')).toBe('Off');
    expect(desktopStatusLabel(false, 'Screen capture unavailable')).toBe('Screen capture unavailable');
    expect(desktopStatusLabel(true, 'Buffering recent frames')).toBe('Buffering recent frames');
  });

  it('accepts only canonical browser modes', () => {
    expect(normalizeResearchMode('disabled')).toBe('disabled');
    expect(normalizeResearchMode('quick')).toBe('quick');
    expect(normalizeResearchMode('deep')).toBe('deep');
    expect(normalizeResearchMode('unknown')).toBe('disabled');
  });

  it('normalizes local workspace picker responses', () => {
    expect(normalizeLocalWorkspaceSelection({ path: 'F:\\\\LLM\\\\omnix', name: 'omnix' })).toEqual({
      path: 'F:\\\\LLM\\\\omnix',
      name: 'omnix',
    });
    expect(normalizeLocalWorkspaceSelection({ path: '/home/dev/project/' })).toEqual({
      path: '/home/dev/project/',
      name: 'project',
    });
    expect(normalizeLocalWorkspaceSelection({ cancelled: true })).toBeNull();
  });

  it('renders local folder as an independent context summary', () => {
    expect(localWorkspaceSummary(normalizeLocalWorkspaceSelection({ path: 'F:\\\\LLM\\\\omnix', name: 'omnix' }))).toBe('Local folder · omnix');
    expect(localWorkspaceSummary(null)).toBe('');
  });

  it('keeps the deep-research page budget within the hard per-run cap', () => {
    expect(normalizeDeepResearchPageLimit(7)).toBe(7);
    expect(normalizeDeepResearchPageLimit(0)).toBe(1);
    expect(normalizeDeepResearchPageLimit(99)).toBe(30);
    expect(normalizeDeepResearchPageLimit('invalid', 9)).toBe(9);
  });

  it('uses the three explicit user-facing labels', () => {
    expect(webResearchModeLabel('disabled')).toBe('Disabled');
    expect(webResearchModeLabel('quick')).toBe('Quick search');
    expect(webResearchModeLabel('deep')).toBe('Deep research');
  });
});
