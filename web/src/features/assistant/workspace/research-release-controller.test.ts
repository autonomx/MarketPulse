import { beforeAll, describe, expect, it } from 'vitest';

let helpers: typeof import('./research-release-controller');

beforeAll(async () => {
  helpers = await import('./research-release-controller');
});

describe('research release controls', () => {
  it('offers downgrade only for unavailable Deep Research with Quick Search available', () => {
    expect(helpers.shouldOfferResearchDowngrade('deep', {
      disabled: true,
      quick: true,
      deep: false,
      hermes_planner: false,
    })).toBe(true);
    expect(helpers.shouldOfferResearchDowngrade('quick', {
      disabled: true,
      quick: true,
      deep: false,
      hermes_planner: false,
    })).toBe(false);
    expect(helpers.shouldOfferResearchDowngrade('deep', {
      disabled: true,
      quick: false,
      deep: false,
      hermes_planner: false,
    })).toBe(false);
  });

  it('adds explicit consent without replacing the selected research mode', () => {
    expect(helpers.addResearchDowngradeConsent({
      content: 'Research this',
      web_research_mode: 'deep',
    }, true)).toEqual({
      content: 'Research this',
      web_research_mode: 'deep',
      allow_research_downgrade: true,
    });
  });

  it('renders a visible unavailable-mode explanation', () => {
    expect(helpers.researchReleaseMessage({
      reason: 'deep_research_disabled_in_settings',
      available_modes: ['disabled', 'quick'],
      downgrade_available: true,
    })).toBe('Deep research disabled in settings. Quick Search is available when fallback is explicitly allowed.');
    expect(helpers.researchReleaseMessage({
      reason: 'research_master_rollback_active',
      available_modes: ['disabled'],
      downgrade_available: false,
    })).toBe('Research master rollback active. Available: disabled.');
  });
});
