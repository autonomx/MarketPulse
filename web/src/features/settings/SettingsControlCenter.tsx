import { useEffect, useRef, useState } from 'react';
import { WorkspacePanel } from '../../design/primitives';
import { CatalogPanel } from './CatalogPanel';
import { ImageSpeechSettings } from './ImageSpeechSettings';
import { ModelSettings } from './ModelSettings';
import { NarrativeSettings } from './NarrativeSettings';
import { OperationsSettings } from './OperationsSettings';
import { OverviewSettings } from './OverviewSettings';
import { RpgDefaultsSettings } from './RpgDefaultsSettings';
import { ServicesSettings } from './ServicesSettings';
import { SettingsActionHeader } from './SettingsActionHeader';
import { SettingsCategoryPanel } from './SettingsCategoryPanel';
import { SettingsCategoryRail } from './SettingsCategoryRail';
import { SettingsProfileProvider } from './SettingsProfileProvider';
import { isSettingsCategoryId, SETTINGS_CATEGORIES } from './settingsRegistry';
import { SettingsStatusRail } from './SettingsStatusRail';
import type { SettingsCategoryId } from './settingsTypes';
import './SettingsControlCenter.css';
import './SettingsComponents.css';
import './SettingsResponsive.css';
import { useSettingsProfileContext } from './SettingsProfileContext';

export function SettingsControlCenter() {
  const [activeCategory, setActiveCategory] = useState<SettingsCategoryId>(() => {
    const requested = new URLSearchParams(window.location.search).get('category');
    return requested && isSettingsCategoryId(requested) ? requested : 'ai-providers';
  });
  const [query, setQuery] = useState('');
  const mainRef = useRef<HTMLElement | null>(null);
  const initialCategory = useRef(true);
  const categoryIndex = SETTINGS_CATEGORIES.findIndex((item) => item.id === activeCategory);
  const category = SETTINGS_CATEGORIES[categoryIndex] ?? SETTINGS_CATEGORIES[0]!;
  const content = activeCategory === 'overview'
    ? <OverviewSettings />
    : activeCategory === 'models-runtime'
      ? <><ModelSettings /><CatalogPanel /></>
      : activeCategory === 'storyteller-podcast'
        ? <NarrativeSettings />
        : activeCategory === 'rpg'
          ? <RpgDefaultsSettings />
          : activeCategory === 'images-speech-input'
            ? <ImageSpeechSettings />
            : activeCategory === 'tools-integrations'
              ? <ServicesSettings />
              : activeCategory === 'jobs-assets-storage'
                ? <OperationsSettings />
                : activeCategory === 'diagnostics-developer'
                  ? <OperationsSettings view="runtime" />
                  : <SettingsCategoryPanel categoryId={activeCategory} />;
  useEffect(() => {
    if (initialCategory.current) {
      initialCategory.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [activeCategory]);
  return (
    <SettingsProfileProvider>
      <WorkspacePanel label="Settings" className="settings-control-panel">
        <a className="settings-skip-link" href="#settings-main">Skip to settings content</a>
        <div className="settings-control-center">
          <SettingsCategoryRail activeCategory={activeCategory} query={query} onQueryChange={setQuery} onSelect={setActiveCategory} />
          <div className="settings-control-content"><SettingsActionHeader /><SettingsLoadError /><main id="settings-main" ref={mainRef} tabIndex={-1} aria-label={`${category.label} settings`} className="settings-main-column">{content}</main></div>
          <SettingsStatusRail />
        </div>
      </WorkspacePanel>
    </SettingsProfileProvider>
  );
}

/** Settings that could not be loaded say so on every category (WP-9.10). */
function SettingsLoadError() {
  const { loadError } = useSettingsProfileContext();
  return loadError ? <p role="alert" className="settings-inline-status">Settings could not be loaded: {loadError}</p> : null;
}
