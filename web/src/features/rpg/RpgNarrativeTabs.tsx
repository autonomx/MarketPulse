import { useEffect, useMemo, useRef, useState } from 'react';
import { VirtualList } from '../../design/VirtualList';
import { RpgLorePanel } from './RpgLorePanel';
import type { RpgJournalDetailPreview, RpgJournalEntryPreview } from './rpgUiState';

type RpgNarrativeTab = 'journal' | 'lore' | 'dialogue' | 'turns';

interface RpgNarrativeTabsProps {
  journalEntries: RpgJournalEntryPreview[];
  logEntries: RpgJournalEntryPreview[];
  journalDetail: RpgJournalDetailPreview;
  recentEvents: string[];
}

const tabs: Array<{ id: RpgNarrativeTab; label: string }> = [
  { id: 'journal', label: 'Journal' },
  { id: 'lore', label: 'Lore' },
  { id: 'dialogue', label: 'Dialogue log' },
  { id: 'turns', label: 'Turn history' },
];

const emptyDialogueRow: RpgJournalEntryPreview = {
  time: 'Dialogue',
  title: 'No dialogue captured yet',
  detail: 'Dialogue appears here after NPC, narrator, or player messages are recorded for this campaign.',
};

const emptyTurnRow: RpgJournalEntryPreview = {
  time: 'Turn history',
  title: 'No replayed turns yet',
  detail: 'Submit a command to start recording replay-safe turn history for this campaign.',
};

export function RpgNarrativeTabs({ journalEntries, logEntries, journalDetail, recentEvents }: RpgNarrativeTabsProps) {
  const [activeTab, setActiveTab] = useState<RpgNarrativeTab>('journal');
  const [selectedJournalIndex, setSelectedJournalIndex] = useState(0);
  const dialogueRows = useMemo(() => buildDialogueRows(logEntries, recentEvents), [logEntries, recentEvents]);
  const turnRows = useMemo(() => buildTurnRows(logEntries), [logEntries]);

  useEffect(() => {
    setSelectedJournalIndex(0);
  }, [journalEntries]);

  return (
    <section className="rpg-card rpg-journal-card">
      <div className="rpg-tabs" role="tablist" aria-label="RPG logs and lore">
        {tabs.map((tab) => (
          <button
            aria-controls={`rpg-${tab.id}-panel`}
            aria-selected={activeTab === tab.id}
            className={activeTab === tab.id ? 'active' : undefined}
            id={`rpg-${tab.id}-tab`}
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>
      {activeTab === 'journal' ? (
        <JournalPanel
          journalDetail={journalDetail}
          journalEntries={journalEntries}
          onSelectedIndexChange={setSelectedJournalIndex}
          selectedIndex={selectedJournalIndex}
        />
      ) : null}
      {activeTab === 'lore' ? <RpgLorePanel /> : null}
      {activeTab === 'dialogue' ? <DialoguePanel dialogueRows={dialogueRows} /> : null}
      {activeTab === 'turns' ? <TurnHistoryPanel turnRows={turnRows} /> : null}
    </section>
  );
}

function JournalPanel({
  journalEntries,
  journalDetail,
  onSelectedIndexChange,
  selectedIndex,
}: Pick<RpgNarrativeTabsProps, 'journalEntries' | 'journalDetail'> & {
  onSelectedIndexChange: (index: number) => void;
  selectedIndex: number;
}) {
  const safeSelectedIndex = journalEntries[selectedIndex] ? selectedIndex : 0;
  const selectedEntry = journalEntries[safeSelectedIndex];
  const detailTitle = selectedEntry?.title ?? journalDetail.title;
  const detailText = selectedEntry?.detail ?? journalDetail.detail;
  // One entry per in-game day: a long campaign's journal renders only the rows in view (WP-9.9).
  const listRef = useRef<HTMLDivElement>(null);
  const indexedEntries = useMemo(() => journalEntries.map((entry, index) => ({ entry, index })), [journalEntries]);

  return (
    <div aria-labelledby="rpg-journal-tab" className="rpg-journal-grid" id="rpg-journal-panel" role="tabpanel">
      <div className="rpg-journal-list" ref={listRef}>
        <VirtualList
          items={indexedEntries}
          getKey={({ entry, index }) => `${index}:${entry.time}:${entry.title}`}
          scrollRef={listRef}
          estimateSize={56}
          renderItem={({ entry, index }) => (
            <article
              aria-pressed={index === safeSelectedIndex}
              className={index === safeSelectedIndex ? 'active' : undefined}
              onClick={() => onSelectedIndexChange(index)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelectedIndexChange(index);
                }
              }}
              role="button"
              tabIndex={0}
            >
              <span aria-hidden="true" />
              <div>
                <strong>{entry.time}</strong>
                <p>{entry.title}</p>
              </div>
            </article>
          )}
        />
      </div>
      <article className="rpg-journal-detail">
        <h3>{detailTitle}</h3>
        <p>{detailText}</p>
        <ul>
          {journalDetail.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}
        </ul>
        <div className="rpg-chip-row">
          {journalDetail.tags.map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      </article>
    </div>
  );
}

function DialoguePanel({ dialogueRows }: { dialogueRows: RpgJournalEntryPreview[] }) {
  return (
    <div aria-labelledby="rpg-dialogue-tab" className="rpg-journal-grid" id="rpg-dialogue-panel" role="tabpanel">
      <div className="rpg-journal-list">
        {dialogueRows.map((row, index) => (
          <article className={index === 0 ? 'active' : undefined} key={`${row.time}:${row.title}`}>
            <span aria-hidden="true" />
            <div><strong>{row.time}</strong><p>{row.title}</p></div>
          </article>
        ))}
      </div>
      <article className="rpg-journal-detail">
        <h3>Dialogue log</h3>
        <p>Conversation-facing messages from the selected RPG session. Setup notes and ambient world events stay in the Journal tab.</p>
        <ul>{dialogueRows.map((row) => <li key={`${row.time}:${row.detail}`}>{row.detail}</li>)}</ul>
        <div className="rpg-chip-row"><span>Conversation</span><span>Messages only</span><span>Replay-safe</span></div>
      </article>
    </div>
  );
}

function TurnHistoryPanel({ turnRows }: { turnRows: RpgJournalEntryPreview[] }) {
  return (
    <div aria-labelledby="rpg-turns-tab" className="rpg-journal-grid" id="rpg-turns-panel" role="tabpanel">
      <div className="rpg-journal-list">
        {turnRows.map((row, index) => (
          <article className={index === 0 ? 'active' : undefined} key={`${row.time}:${row.title}`}>
            <span aria-hidden="true" />
            <div><strong>{row.time}</strong><p>{row.title}</p></div>
          </article>
        ))}
      </div>
      <article className="rpg-journal-detail">
        <h3>Turn history</h3>
        <p>Replay-safe player commands and queued turn requests for the selected RPG session.</p>
        <ul>{turnRows.map((row) => <li key={`${row.time}:${row.detail}`}>{row.detail}</li>)}</ul>
        <div className="rpg-chip-row"><span>Deterministic</span><span>Commands only</span><span>Checkpoint aware</span></div>
      </article>
    </div>
  );
}

function buildDialogueRows(journalEntries: RpgJournalEntryPreview[], recentEvents: string[]): RpgJournalEntryPreview[] {
  const dialogueLikeEntries = journalEntries.filter((entry) => isDialogueEntry(entry));
  if (dialogueLikeEntries.length) return dialogueLikeEntries;
  const dialogueLikeEvents = recentEvents
    .map((event, index) => ({ time: `Event ${index + 1}`, title: speakerFromEvent(event) ?? 'Narration', detail: event }))
    .filter((entry) => isDialogueEntry(entry));
  return dialogueLikeEvents.length ? dialogueLikeEvents.slice(0, 6) : [emptyDialogueRow];
}

function buildTurnRows(journalEntries: RpgJournalEntryPreview[]): RpgJournalEntryPreview[] {
  const turnLikeEntries = journalEntries.filter((entry) => isTurnHistoryEntry(entry));
  if (!turnLikeEntries.length) return [emptyTurnRow];
  return turnLikeEntries.slice(0, 6).map((entry, index) => ({
    time: entry.time || `Turn ${index + 1}`,
    title: normalizeTurnTitle(entry.title, index),
    detail: entry.detail,
  }));
}

function isDialogueEntry(entry: RpgJournalEntryPreview): boolean {
  return isDialogueLike(entry.title) || isDialogueLike(entry.detail);
}

function isDialogueLike(value: string): boolean {
  const normalized = value.toLowerCase();
  return normalized.includes('dialogue') || normalized.includes('message') || normalized.includes('replied') || normalized.includes('speaks') || normalized.includes('says') || value.includes(':');
}

function isTurnHistoryEntry(entry: RpgJournalEntryPreview): boolean {
  const normalized = `${entry.title} ${entry.detail}`.toLowerCase();
  return /\b(?:player message|player command|command|queued turn|completed turn|turn job|rpg\.turn|submitted command)\b/.test(normalized);
}

function normalizeTurnTitle(title: string, index: number): string {
  return title.trim() ? title : `Turn ${index + 1}`;
}

function speakerFromEvent(event: string): string | undefined {
  const [speaker] = event.split(':');
  return speaker && speaker !== event ? speaker.trim() : undefined;
}
