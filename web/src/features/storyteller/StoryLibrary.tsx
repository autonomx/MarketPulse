/** The library sidebar: drafts, stories and trash. */
import { StoryLibraryItem, StoryLibrarySection, TrashedStoryLibraryItem, librarySectionLabel, librarySections } from './storyModel';

export function StoryLibrary({
  items,
  activeItemId,
  activeSection,
  trashItems,
  onNewDraft,
  onSectionChange,
  onSelect,
  onTrashActiveItem,
}: {
  items: StoryLibraryItem[];
  activeItemId: string | null;
  activeSection: StoryLibrarySection;
  trashItems: TrashedStoryLibraryItem[];
  onNewDraft: () => void;
  onSectionChange: (section: StoryLibrarySection) => void;
  onSelect: (itemId: string) => void;
  onTrashActiveItem: () => void;
}) {
  const sectionItems = activeSection === 'drafts'
    ? items.filter((item) => item.source === 'draft')
    : activeSection === 'stories'
      ? items.filter((item) => item.source !== 'draft')
      : activeSection === 'trash'
        ? trashItems
        : [];
  return (
    <aside className="storyteller-library" aria-label="Story library">
      <div className="storyteller-panel-heading compact">
        <p className="eyebrow">Library</p>
        <button aria-label="New story draft" type="button" onClick={onNewDraft}>+</button>
      </div>
      <nav aria-label="Story library sections">
        {librarySections.map((section) => (
          <button
            aria-pressed={activeSection === section.id}
            className={activeSection === section.id ? 'active' : ''}
            key={section.id}
            type="button"
            onClick={() => onSectionChange(section.id)}
          >
            {section.label}
          </button>
        ))}
      </nav>
      <section className="storyteller-library-section" aria-label={`${librarySectionLabel(activeSection)} library pane`}>
        <p className="eyebrow">{librarySectionLabel(activeSection)}</p>
        {sectionItems.length ? (
          <div className="storyteller-recent-list">
            {sectionItems.map((item) => (
              <LibraryItemButton activeItemId={activeItemId} item={item} key={item.id} onSelect={onSelect} />
            ))}
          </div>
        ) : (
          <LibrarySectionEmpty section={activeSection} onNewDraft={onNewDraft} />
        )}
      </section>
      <section>
        <p className="eyebrow">Recent stories</p>
        <div className="storyteller-recent-list">
          {items.length ? (
            items.slice(0, 6).map((item) => (
              <LibraryItemButton activeItemId={activeItemId} item={item} key={item.id} onSelect={onSelect} />
            ))
          ) : (
            <article>
              <span className="storyteller-thumb muted" />
              <div><strong>No stories yet</strong><small>Generate or save a story</small></div>
            </article>
          )}
        </div>
      </section>
      <button
        aria-pressed={activeSection === 'trash'}
        className={`storyteller-trash ${activeSection === 'trash' ? 'active' : ''}`}
        type="button"
        title={activeSection === 'trash' ? 'Trash' : 'Move selected story to Trash'}
        onClick={onTrashActiveItem}
      >
        Trash
      </button>
    </aside>
  );
}

export function LibraryItemButton({ item, activeItemId, onSelect }: {
  item: StoryLibraryItem;
  activeItemId: string | null;
  onSelect: (itemId: string) => void;
}) {
  return (
    <button
      aria-pressed={item.id === activeItemId}
      className={item.id === activeItemId ? 'active' : ''}
      type="button"
      onClick={() => onSelect(item.id)}
    >
      <span className={`storyteller-thumb ${item.source === 'asset' ? 'muted' : ''}`} />
      <div><strong>{item.title}</strong><small>{item.subtitle}</small></div>
    </button>
  );
}

export function LibrarySectionEmpty({ section, onNewDraft }: { section: StoryLibrarySection; onNewDraft: () => void }) {
  const copy: Record<StoryLibrarySection, { title: string; body: string }> = {
    drafts: { title: 'No saved drafts', body: 'Start a blank draft or save the active manuscript.' },
    stories: { title: 'No story assets yet', body: 'Generated and saved stories will appear here.' },
    characters: { title: 'Characters not created yet', body: 'Character cards will attach cast details to future generations.' },
    'world-notes': { title: 'World notes not created yet', body: 'World notes will collect lore, places, factions, and rules.' },
    prompts: { title: 'Prompt presets not created yet', body: 'Prompt presets will save reusable Storyteller instructions.' },
    trash: { title: 'Trash is empty', body: 'Stories moved to Trash will be hidden from recent stories.' },
  };
  return (
    <article className="storyteller-library-empty" role="status">
      <strong>{copy[section].title}</strong>
      <small>{copy[section].body}</small>
      {section === 'drafts' ? <button type="button" onClick={onNewDraft}>Start blank draft</button> : null}
    </article>
  );
}
