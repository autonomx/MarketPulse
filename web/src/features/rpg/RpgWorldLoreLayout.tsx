import type { CSSProperties, ReactNode } from 'react';
import type { RpgAuthoringDocumentBlock } from './api/rpgWorldAuthoringClient';
import { RpgWorldDocumentBlock } from './RpgWorldDocumentBlocks';
import './RpgWorldLoreLayout.css';

interface RpgWorldLoreLayoutProps {
  blocks: RpgAuthoringDocumentBlock[];
  children?: ReactNode;
  heroStyle?: CSSProperties;
  sectionId: string;
  summary: string;
  title: string;
  toc: Array<{ id: string; label: string }>;
}

const HISTORY_LAYOUT = {
  eyebrow: 'Living chronicle',
  icon: '▣',
  mode: 'chronicle',
  note: 'Introductory history remains readable prose, while eras and turning points follow an ordered timeline of cause and consequence.',
};

const CALENDAR_LAYOUT = {
  eyebrow: 'Calendar and eras',
  icon: '◷',
  mode: 'chronicle',
  note: 'Timekeeping is explained in prose, with eras, seasons, festivals, and recurring observances arranged chronologically.',
};

const LAYOUTS: Record<string, { eyebrow: string; icon: string; mode: string; note: string }> = {
  realm: {
    eyebrow: 'Realm dossier',
    icon: '⌂',
    mode: 'realm',
    note: 'Identity, defining truths, major powers, and places that anchor the setting.',
  },
  realm_overview: {
    eyebrow: 'Realm dossier',
    icon: '⌂',
    mode: 'realm',
    note: 'Identity, defining truths, major powers, and places that anchor the setting.',
  },
  cosmology: {
    eyebrow: 'World laws',
    icon: '◊',
    mode: 'cosmology',
    note: 'Planes, origins, metaphysical laws, and the boundaries of accepted reality.',
  },
  magic_technology: {
    eyebrow: 'Systems of power',
    icon: '⋈',
    mode: 'systems',
    note: 'Sources, practices, costs, institutions, and consequences of exceptional power.',
  },
  history: HISTORY_LAYOUT,
  history_timeline: HISTORY_LAYOUT,
  calendar: CALENDAR_LAYOUT,
  calendar_and_eras: CALENDAR_LAYOUT,
  cultures: {
    eyebrow: 'Peoples and traditions',
    icon: '♙',
    mode: 'culture',
    note: 'Values, customs, languages, tensions, and everyday practices across the world.',
  },
  institutions: {
    eyebrow: 'Institutions and orders',
    icon: '▤',
    mode: 'powers',
    note: 'Organizations that preserve knowledge, exercise authority, or shape daily life.',
  },
  pantheon: {
    eyebrow: 'Faith and divinity',
    icon: '✺',
    mode: 'pantheon',
    note: 'Religions, divine figures, rites, competing interpretations, and sacred places.',
  },
  hero_system: {
    eyebrow: 'Exceptional powers',
    icon: '✵',
    mode: 'systems',
    note: 'How heroes, champions, summoning, and extraordinary advancement fit established canon.',
  },
  current_conflicts: {
    eyebrow: 'Active pressures',
    icon: '⚔',
    mode: 'conflicts',
    note: 'Contested goals, affected regions, involved powers, and consequences already in motion.',
  },
};

function pullQuote(blocks: RpgAuthoringDocumentBlock[]): string {
  const candidate = blocks
    .map((block) => String(block.body ?? '').trim())
    .find((body) => body.length >= 90);
  if (!candidate) return '';
  const firstParagraph = candidate.split(/\n\s*\n/)[0].trim();
  const firstSentence = firstParagraph.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim() ?? firstParagraph;
  return firstSentence.length > 240 ? `${firstSentence.slice(0, 237).trim()}…` : firstSentence;
}

export function RpgWorldLoreLayout({
  blocks,
  children,
  heroStyle,
  sectionId,
  summary,
  title,
  toc,
}: RpgWorldLoreLayoutProps) {
  const layout = LAYOUTS[sectionId] ?? {
    eyebrow: 'World lore',
    icon: '✦',
    mode: 'document',
    note: 'A structured record of established canon and connected world knowledge.',
  };
  const quote = pullQuote(blocks);
  const chronicle = layout.mode === 'chronicle';

  return (
    <div className={`rpg-authoring-document-shell${chronicle ? ' is-chronicle' : ''} rpg-lore-layout is-${layout.mode}`}>
      <article className="rpg-lore-layout-stream">
        <header className={`rpg-lore-hero${heroStyle ? ' has-image' : ''}`} style={heroStyle}>
          <div className="rpg-lore-hero-mark" aria-hidden="true">{layout.icon}</div>
          <div>
            <p className="eyebrow">{layout.eyebrow}</p>
            <h2>{title}</h2>
            <p className="rpg-lore-summary">{summary}</p>
          </div>
          <aside>
            <strong>Reading guide</strong>
            <p>{layout.note}</p>
            <span>{blocks.length} canon section{blocks.length === 1 ? '' : 's'}</span>
          </aside>
        </header>

        {quote ? <blockquote className="rpg-lore-pull-quote">“{quote}”</blockquote> : null}

        <div className="rpg-lore-blocks">
          {blocks.length ? blocks.map((block, index) => (
            <section className="rpg-lore-block-anchor" id={toc[index].id} key={`${toc[index].id}:${index}`}>
              <RpgWorldDocumentBlock block={block} />
            </section>
          )) : (
            <div className="rpg-authoring-empty">
              <h3>Not generated yet</h3>
              <p>This section will populate as world generation completes.</p>
            </div>
          )}
        </div>
        {children}
      </article>

      {toc.length ? (
        <nav className="rpg-lore-toc" aria-label={`${title} sections`}>
          <strong>On this page</strong>
          <p>{layout.note}</p>
          {toc.map((item, index) => (
            <a href={`#${item.id}`} key={item.id}><span aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>{item.label}</a>
          ))}
        </nav>
      ) : null}
    </div>
  );
}
