export type AssistantWorkspaceRegion = 'top_bar' | 'left_nav' | 'timeline' | 'right_panel' | 'composer';

export type AssistantAppLayout = {
  regions: AssistantWorkspaceRegion[];
  rightPanelVisible: boolean;
  leftNavVisible: boolean;
};

export const DEFAULT_ASSISTANT_APP_REGIONS: AssistantWorkspaceRegion[] = [
  'top_bar',
  'left_nav',
  'timeline',
  'right_panel',
  'composer',
];

export function createAssistantAppLayout(input: Partial<AssistantAppLayout> = {}): AssistantAppLayout {
  return {
    regions: input.regions ? [...input.regions] : [...DEFAULT_ASSISTANT_APP_REGIONS],
    rightPanelVisible: input.rightPanelVisible ?? true,
    leftNavVisible: input.leftNavVisible ?? true,
  };
}

export function getVisibleRegions(layout: AssistantAppLayout): AssistantWorkspaceRegion[] {
  return layout.regions.filter((region) => {
    if (region === 'right_panel') return layout.rightPanelVisible;
    if (region === 'left_nav') return layout.leftNavVisible;
    return true;
  });
}
