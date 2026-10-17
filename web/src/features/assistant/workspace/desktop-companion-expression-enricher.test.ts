import { describe, expect, it } from 'vitest';

import { expressionForDesktopAttention } from './desktop-companion-expression-enricher';

describe('desktop companion expression mapping', () => {
  it('maps glance and deep attention to distinct character cues', () => {
    expect(expressionForDesktopAttention({ reaction: 'glance' })).toEqual({
      expression: 'curious',
      intensity: 0.5,
      critical: false,
    });
    expect(expressionForDesktopAttention({ reaction: 'deep' })).toEqual({
      expression: 'focused',
      intensity: 0.75,
      critical: false,
    });
  });

  it('promotes strongly grounded high-importance reactions to alert critical delivery', () => {
    expect(expressionForDesktopAttention({
      reaction: 'deep',
      rationale: 'scene_change,high_importance,strong_visual_support',
    })).toEqual({
      expression: 'alert',
      intensity: 0.95,
      critical: true,
    });
  });
});
