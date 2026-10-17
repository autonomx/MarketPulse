import type {
  RpgAuthoringDocumentBlock,
  RpgAuthoringSection,
} from './api/rpgWorldAuthoringClient';

export interface RpgWorldEditorRoute {
  worldId: string;
  sectionId: string;
  entityId?: string;
}

const AUTHORING_GROUP_ORDER: RpgAuthoringSection['group'][] = [
  'workspace',
  'world',
  'lore',
  'game-master',
];

export function completeAuthoringSections(sections: RpgAuthoringSection[]): RpgAuthoringSection[] {
  const groupRank = new Map<RpgAuthoringSection['group'], number>(
    AUTHORING_GROUP_ORDER.map((group, index) => [group, index] as const),
  );
  return sections
    .map((section, index) => ({ section, index }))
    .sort((left, right) => {
      const groupDifference = (groupRank.get(left.section.group) ?? 99)
        - (groupRank.get(right.section.group) ?? 99);
      return groupDifference || left.index - right.index;
    })
    .map(({ section }) => section);
}

export function parseWorldEditorRoute(search = typeof window === 'undefined' ? '' : window.location.search): RpgWorldEditorRoute | null {
  const query = search.startsWith('?') ? search.slice(1) : search;
  const params = new URLSearchParams(query);
  const worldId = params.get('rpg-world')?.trim() ?? '';
  if (!worldId) return null;
  return {
    worldId,
    sectionId: params.get('section')?.trim() || 'overview',
    entityId: params.get('entity')?.trim() || undefined,
  };
}

export function worldEditorSearch(route: RpgWorldEditorRoute | null, currentSearch = ''): string {
  const params = new URLSearchParams(currentSearch.startsWith('?') ? currentSearch.slice(1) : currentSearch);
  params.delete('rpg-world');
  params.delete('section');
  params.delete('entity');
  if (route) {
    params.set('rpg-world', route.worldId);
    params.set('section', route.sectionId || 'overview');
    if (route.entityId) params.set('entity', route.entityId);
  }
  const query = params.toString();
  return query ? `?${query}` : '';
}

export function pushWorldEditorRoute(route: RpgWorldEditorRoute | null, replace = false): void {
  if (typeof window === 'undefined') return;
  const next = `${window.location.pathname}${worldEditorSearch(route, window.location.search)}`;
  if (replace) window.history.replaceState(route, '', next);
  else window.history.pushState(route, '', next);
}

function slug(value: string, fallback: string): string {
  const normalized = value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return normalized || fallback;
}

export function documentAnchors(blocks: RpgAuthoringDocumentBlock[]): Array<{ id: string; label: string }> {
  const counts = new Map<string, number>();
  return blocks.map((block, index) => {
    const label = block.title || block.kind.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
    const base = slug(label, `section-${index + 1}`);
    const occurrence = counts.get(base) ?? 0;
    counts.set(base, occurrence + 1);
    return { id: occurrence ? `${base}-${occurrence + 1}` : base, label };
  });
}

const CHRONICLE_IDS = new Set([
  'history',
  'history_timeline',
  'calendar',
  'calendar_and_eras',
]);

const PROSE_CHRONICLE_HEADINGS = /^(?:overview|introduction|how time is measured|timekeeping|calendar structure|cycles|days and weeks|months and seasons|dating conventions|historical context)$/i;
const TEMPORAL_HEADING = /(?:\b\d{2,4}\b|\bage\b|\bera\b|\bepoch\b|\bperiod\b|\bcentury\b|\byear\b|\btimeline\b|\bchronology\b|\bturning points?\b|\bobservances?\b|\bfestivals?\b)/i;
const MARKDOWN_HEADING = /^(#{1,6})\s+(.+?)\s*$/;
const SIMPLE_HEADING = /^[A-Z][^.!?]{1,78}:?$/;

export function isChronicleSection(sectionId: string): boolean {
  return CHRONICLE_IDS.has(sectionId);
}

function cleanHeading(value: string): string {
  return value.replace(/^#{1,6}\s+/, '').replace(/:\s*$/, '').trim();
}

function isSimpleHeading(lines: string[], index: number): boolean {
  const line = lines[index]?.trim() ?? '';
  if (!line || !SIMPLE_HEADING.test(line) || line.split(/\s+/).length > 10) return false;
  const beforeBlank = index === 0 || !(lines[index - 1] ?? '').trim();
  const afterBlank = index === lines.length - 1 || !(lines[index + 1] ?? '').trim();
  return beforeBlank && afterBlank;
}

function splitHeadedSection(block: RpgAuthoringDocumentBlock): RpgAuthoringDocumentBlock[] {
  const sourceBody = block.body?.trim();
  if (block.kind !== 'section' || !sourceBody) return [block];
  const lines = sourceBody.replace(/\r\n/g, '\n').split('\n');
  const sections: Array<{ title: string; lines: string[] }> = [];
  let current = { title: block.title || 'Overview', lines: [] as string[] };
  let foundHeading = false;

  lines.forEach((line, index) => {
    const markdown = line.trim().match(MARKDOWN_HEADING);
    const simple = !markdown && isSimpleHeading(lines, index);
    if (markdown || simple) {
      const body = current.lines.join('\n').trim();
      if (body) sections.push({ ...current, lines: [body] });
      current = {
        title: cleanHeading(markdown?.[2] ?? line.trim()),
        lines: [],
      };
      foundHeading = true;
      return;
    }
    current.lines.push(line);
  });

  const finalBody = current.lines.join('\n').trim();
  if (finalBody) sections.push({ ...current, lines: [finalBody] });
  if (!foundHeading || sections.length < 2) return [block];

  return sections.map<RpgAuthoringDocumentBlock>((section) => ({
    kind: 'section',
    title: section.title,
    body: section.lines.join('\n\n').trim(),
  }));
}

function hasTemporalMetadata(item: Record<string, unknown>): boolean {
  return [
    item.date,
    item.year,
    item.era,
    item.epoch,
    item.period,
    item.range,
    item.start_year,
    item.end_year,
    item.season,
    item.month,
  ].some((value) => value != null && String(value).trim());
}

function isChronicleProse(block: RpgAuthoringDocumentBlock): boolean {
  return Boolean(block.title && PROSE_CHRONICLE_HEADINGS.test(block.title.trim()));
}

function chronicleBlock(block: RpgAuthoringDocumentBlock): RpgAuthoringDocumentBlock {
  if (block.kind === 'json' || block.kind === 'timeline') return block;
  if (block.kind === 'section' && isChronicleProse(block)) return block;

  if (block.items?.length) {
    const temporal = block.items.some((item) => hasTemporalMetadata(item));
    if (temporal || block.kind === 'facts' || block.kind === 'records' || TEMPORAL_HEADING.test(block.title ?? '')) {
      return { ...block, kind: 'timeline' };
    }
  }

  if (block.kind === 'section' && (TEMPORAL_HEADING.test(block.title ?? '') || block.body)) {
    const timelineItem: Record<string, unknown> = {
      title: block.title || 'Chronicle entry',
      era: TEMPORAL_HEADING.test(block.title ?? '') ? block.title : undefined,
      body: block.body,
    };
    return {
      kind: 'timeline',
      title: block.title || 'Chronicle',
      items: [timelineItem],
    };
  }
  return block;
}

export function presentLoreBlocks(
  sectionId: string,
  blocks: RpgAuthoringDocumentBlock[],
): RpgAuthoringDocumentBlock[] {
  const headed = blocks.flatMap(splitHeadedSection);
  if (CHRONICLE_IDS.has(sectionId)) {
    return headed.map(chronicleBlock);
  }
  if (sectionId === 'realm' || sectionId === 'realm_overview') {
    return headed.map<RpgAuthoringDocumentBlock>((block, index) => ({
      ...block,
      kind: block.kind === 'json' ? 'json' : index === 0 ? 'realm-summary' : block.kind,
      title: block.title || (index === 0 ? 'Realm identity' : undefined),
    }));
  }
  return headed;
}
