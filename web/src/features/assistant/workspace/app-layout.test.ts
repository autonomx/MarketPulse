import { describe, expect, it } from 'vitest';
import { createAssistantAppLayout, getVisibleRegions } from './app-layout';

describe('assistant app layout contracts', () => {
  it('creates the default workspace layout', () => {
    expect(createAssistantAppLayout().regions).toEqual([
      'top_bar',
      'left_nav',
      'timeline',
      'right_panel',
      'composer',
    ]);
  });

  it('hides collapsed regions', () => {
    const layout = createAssistantAppLayout({ rightPanelVisible: false });
    expect(getVisibleRegions(layout)).not.toContain('right_panel');
    expect(getVisibleRegions(layout)).toContain('timeline');
  });
});
