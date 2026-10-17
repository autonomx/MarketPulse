import { INITIAL_SETTINGS_REGISTRY, SETTINGS_CATEGORIES } from './settingsRegistry';
import type { SettingsCategoryId } from './settingsTypes';

const categoryGlyphs: Partial<Record<SettingsCategoryId, string>> = {
  overview: '▦', 'appearance-accessibility': '▣', 'ai-providers': '⌘', 'models-runtime': '▧', 'assistant-chat': '◯', 'voice-audio': '≋', 'storyteller-podcast': '▤', rpg: '◇', 'images-speech-input': '▨', 'tools-integrations': '✣', 'jobs-assets-storage': '▥', 'diagnostics-developer': '<>',
};

categoryGlyphs['trading-market-data'] = '▥';

export function SettingsCategoryRail({ activeCategory, query, onQueryChange, onSelect }: {
  activeCategory: SettingsCategoryId;
  query: string;
  onQueryChange: (value: string) => void;
  onSelect: (categoryId: SettingsCategoryId) => void;
}) {
  const normalized = query.trim().toLowerCase();
  const matchedCategoryIds = new Set(INITIAL_SETTINGS_REGISTRY.filter((setting) => [setting.label, setting.description, setting.key, ...(setting.searchAliases ?? [])].filter(Boolean).join(' ').toLowerCase().includes(normalized)).map((setting) => setting.categoryId));
  const categories = SETTINGS_CATEGORIES.filter((category) => !normalized || matchedCategoryIds.has(category.id) || [category.label, category.description, ...(category.searchAliases ?? [])].join(' ').toLowerCase().includes(normalized));
  return (
    <aside className="settings-category-rail" aria-label="Settings categories">
      <div className="settings-category-heading">Settings</div>
      <label className="settings-rail-search"><span aria-hidden="true">⌕</span><input type="search" aria-label="Search settings categories" value={query} onChange={(event) => onQueryChange(event.currentTarget.value)} placeholder="Search settings…" /></label>
      <span className="settings-search-result-count" aria-live="polite">{normalized ? `${categories.length} matching categories` : `${categories.length} categories`}</span>
      <nav aria-label="Settings category list">{categories.map((category) => <button key={category.id} className={category.id === activeCategory ? 'active' : undefined} type="button" aria-current={category.id === activeCategory ? 'page' : undefined} onClick={() => onSelect(category.id)}><span className="settings-category-icon" aria-hidden="true">{categoryGlyphs[category.id]}</span><span>{category.label}</span></button>)}</nav>
      {!categories.length ? <p className="settings-rail-empty">No matching settings.</p> : null}
    </aside>
  );
}
