/** Story text: added scenes, the outline, text blocks, story-mode pages and the Markdown export. */
import { type JobRecord } from '../../api/client';
import { fullJobOutputText, fullJobOutputTitle } from './storyLibraryModel';
import { StoryGenerationRequest, StoryOutlineChapter, StorySceneAddition, StoryTextBlock, cleanStoryTitle, storySceneAdditionsStorageKey, titleFromStoryText, truncate, jobInputString } from './storyModel';

export function readStorySceneAdditions(): StorySceneAddition[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(storySceneAdditionsStorageKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(toStorySceneAddition).filter((addition): addition is StorySceneAddition => Boolean(addition));
  } catch {
    return [];
  }
}

export function persistStorySceneAdditions(additions: StorySceneAddition[]): void {
  if (typeof window === 'undefined') return;
  try {
    if (additions.length) {
      window.localStorage.setItem(storySceneAdditionsStorageKey, JSON.stringify(additions));
    } else {
      window.localStorage.removeItem(storySceneAdditionsStorageKey);
    }
  } catch {
    // Local scene history is best-effort; the active view still updates from React state.
  }
}

export function toStorySceneAddition(value: unknown): StorySceneAddition | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (
    typeof record.id !== 'string' ||
    typeof record.sourceItemId !== 'string' ||
    typeof record.sourceJobId !== 'string' ||
    typeof record.content !== 'string' ||
    (!record.content.trim() && record.startsNewChapter !== true)
  ) {
    return null;
  }
  return {
    id: record.id,
    sourceItemId: record.sourceItemId,
    sourceJobId: record.sourceJobId,
    chapterNumber: typeof record.chapterNumber === 'number' ? record.chapterNumber : 1,
    sceneNumber: typeof record.sceneNumber === 'number' ? record.sceneNumber : 1,
    title: typeof record.title === 'string' && record.title.trim() ? record.title : 'Continuation',
    chapterTitle: typeof record.chapterTitle === 'string' && record.chapterTitle.trim() ? record.chapterTitle : undefined,
    startsNewChapter: record.startsNewChapter === true,
    content: record.content,
    storyTitle: typeof record.storyTitle === 'string' && record.storyTitle.trim() ? record.storyTitle : null,
    createdAt: typeof record.createdAt === 'string' ? record.createdAt : new Date(0).toISOString(),
  };
}

export function isSceneAppendRequest(request: StoryGenerationRequest): boolean {
  return request.action === 'continue' && Boolean(request.sourceLibraryItemId);
}

export function isStorySceneAppendJob(job: JobRecord): boolean {
  return jobInputString(job, 'action') === 'continue' && Boolean(jobInputString(job, 'source_library_item_id'));
}

export function buildStorySceneAddition(job: JobRecord, request: StoryGenerationRequest): StorySceneAddition | null {
  const sourceItemId = request.sourceLibraryItemId;
  const rawContent = fullJobOutputText(job);
  if (!sourceItemId || !rawContent) return null;
  const storyTitle = cleanStoryTitle(request.values.title) ??
    fullJobOutputTitle(job) ??
    titleFromStoryText(rawContent) ??
    cleanStoryTitle(request.sourceStoryTitle);
  const outline = deriveStoryOutline(request.sourceText, storyTitle ?? request.sourceStoryTitle ?? 'Untitled story');
  const latestChapter = outline[outline.length - 1] ?? { number: 1, scenes: [] };
  const sceneNumber = latestChapter.scenes.filter((scene) => !scene.placeholder).length + 1;
  const normalized = normalizeContinuationSceneContent(rawContent);
  return {
    id: `scene:${sourceItemId}:${job.id}`,
    sourceItemId,
    sourceJobId: job.id,
    chapterNumber: latestChapter.number,
    sceneNumber,
    title: normalized.title ?? sceneTitleFromText(normalized.content) ?? 'Continuation',
    content: normalized.content,
    storyTitle,
    createdAt: new Date().toISOString(),
  };
}

export function upsertStorySceneAddition(additions: StorySceneAddition[], addition: StorySceneAddition): StorySceneAddition[] {
  return [...additions.filter((entry) => entry.id !== addition.id), addition];
}

export function applyStorySceneAdditions(baseText: string | null, additions: StorySceneAddition[]): string | null {
  if (!additions.length) return baseText;
  return additions
    .slice()
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
    .reduce((storyText, addition) => {
      const chapterBlock = addition.startsNewChapter
        ? `Chapter ${addition.chapterNumber}: ${addition.chapterTitle ?? `Chapter ${addition.chapterNumber}`}`
        : null;
      const sceneBlock = [addition.content.trim() || !addition.startsNewChapter ? `Scene ${addition.sceneNumber}: ${addition.title}` : null, addition.content.trim()]
        .filter(Boolean)
        .join('\n\n');
      return [storyText?.trim(), chapterBlock, sceneBlock].filter(Boolean).join('\n\n');
    }, baseText?.trim() ?? '');
}

export function normalizeContinuationSceneContent(text: string): { title: string | null; content: string } {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  while (lines.length && !lines[0].trim()) lines.shift();
  if (lines[0]?.trim().startsWith('# ')) {
    lines.shift();
    while (lines.length && !lines[0].trim()) lines.shift();
  }
  const firstCleaned = lines[0]?.replace(/^#{1,4}\s*/, '').trim() ?? '';
  if (/^chapter\s+\d+/i.test(firstCleaned)) {
    lines.shift();
    while (lines.length && !lines[0].trim()) lines.shift();
  }
  const sceneMatch = lines[0]?.replace(/^#{1,4}\s*/, '').trim().match(/^scene\s+\d+\s*[:\-]?\s*(.*)$/i);
  const title = sceneMatch?.[1]?.trim() || null;
  if (sceneMatch) {
    lines.shift();
    while (lines.length && !lines[0].trim()) lines.shift();
  }
  const content = lines.join('\n').trim() || text.trim();
  return { title: cleanStoryTitle(title), content };
}

export function sceneTitleFromText(text: string): string | null {
  const firstParagraph = storyParagraphs(text)[0]?.replace(/^#{1,4}\s*/, '').trim();
  if (!firstParagraph) return null;
  const sentence = firstParagraph.split(/[.!?]/)[0]?.trim();
  return sentence ? truncate(sentence, 48) : null;
}

export function deriveStoryOutline(text: string | null, fallbackTitle: string): StoryOutlineChapter[] {
  if (!text?.trim()) {
    return [{ id: 'story-chapter-1', number: 1, label: 'Chapter 1', title: fallbackTitle, scenes: [{ id: 'story-scene-1-1', label: 'Scene 1', title: 'Opening' }] }];
  }
  const chapters: StoryOutlineChapter[] = [];
  let current: StoryOutlineChapter | null = null;
  let sawContentBeforeFirstChapter = false;
  text.split(/\n+/).forEach((line) => {
    const cleaned = line.replace(/^#{1,4}\s*/, '').trim();
    const chapterMatch = cleaned.match(/^chapter\s+(\d+)\s*[:\-–—]?\s*(.*)$/i);
    const sceneMatch = cleaned.match(/^scene\s+(\d+)\s*[:\-–—]?\s*(.*)$/i);
    if (chapterMatch) {
      const number = Number(chapterMatch[1]);
      if (!chapters.length && sawContentBeforeFirstChapter && number !== 1) {
        chapters.push({
          id: 'story-chapter-1',
          number: 1,
          label: 'Chapter 1',
          title: fallbackTitle,
          scenes: [{ id: 'story-scene-1-1', label: 'Scene 1', title: 'Opening' }],
        });
      }
      current = {
        id: `story-chapter-${number}`,
        number,
        label: `Chapter ${number}`,
        title: chapterMatch[2]?.trim() || `Chapter ${number}`,
        scenes: [],
      };
      chapters.push(current);
      return;
    }
    if (cleaned && !current && !chapters.length && !sceneMatch) {
      sawContentBeforeFirstChapter = true;
    }
    if (sceneMatch && !current) {
      current = {
        id: 'story-chapter-1',
        number: 1,
        label: 'Chapter 1',
        title: fallbackTitle,
        scenes: [],
      };
      chapters.push(current);
    }
    if (sceneMatch && current) {
      const sceneNumber = Number(sceneMatch[1]);
      current.scenes.push({
        id: `story-scene-${current.number}-${sceneNumber}`,
        label: `Scene ${sceneNumber}`,
        title: sceneMatch[2]?.trim() || `Scene ${sceneNumber}`,
      });
    }
  });
  if (!chapters.length) {
    chapters.push({
      id: 'story-chapter-1',
      number: 1,
      label: 'Chapter 1',
      title: fallbackTitle,
      scenes: [{ id: 'story-scene-1-1', label: 'Scene 1', title: 'Opening' }],
    });
  }
  return chapters.map((chapter) => ({
    ...chapter,
    scenes: chapter.scenes.length ? chapter.scenes : [{ id: `story-scene-${chapter.number}-1`, label: 'Scene 1', title: 'Opening', placeholder: true }],
  }));
}

export function storyTextBlocks(text: string, outline: StoryOutlineChapter[]): StoryTextBlock[] {
  let chapterIndex = 0;
  const sceneIndexByChapter = new Map<number, number>();
  return text.split(/\n{2,}|\r?\n/).map((raw) => raw.trim()).filter(Boolean).map((line) => {
    const cleaned = line.replace(/^#{1,4}\s*/, '').trim();
    const chapterMatch = cleaned.match(/^chapter\s+(\d+)\s*[:\-–—]?\s*(.*)$/i);
    const sceneMatch = cleaned.match(/^scene\s+(\d+)\s*[:\-–—]?\s*(.*)$/i);
    if (chapterMatch) {
      const number = Number(chapterMatch[1]) || ++chapterIndex;
      chapterIndex = number;
      return { kind: 'chapter', id: `story-chapter-${number}`, text: chapterMatch[2]?.trim() || `Chapter ${number}` };
    }
    if (sceneMatch) {
      const activeChapter = outline[Math.max(0, chapterIndex - 1)] ?? outline[0];
      const chapterNumber = activeChapter?.number ?? 1;
      const sceneNumber = Number(sceneMatch[1]) || (sceneIndexByChapter.get(chapterNumber) ?? 0) + 1;
      sceneIndexByChapter.set(chapterNumber, sceneNumber);
      return { kind: 'scene', id: `story-scene-${chapterNumber}-${sceneNumber}`, text: sceneMatch[2]?.trim() || `Scene ${sceneNumber}` };
    }
    return { kind: 'paragraph', text: cleaned };
  });
}

export function storyParagraphs(text: string): string[] {
  return text.split(/\n{2,}/).map((paragraph) => paragraph.trim()).filter(Boolean);
}

export function lastStoryPage(text: string): string {
  const chunks = storyParagraphs(text);
  return chunks.slice(Math.max(0, chunks.length - 5)).join('\n\n');
}

export function suggestedStoryMoves(text: string | null, title: string): string[] {
  if (!text) {
    return ['Begin the story quietly', 'Open with a mystery', 'Introduce the main character'];
  }
  const lower = `${title} ${text}`.toLowerCase();
  if (lower.includes('door')) return ['Open the door carefully', 'Listen before entering', 'Mark the doorway and leave'];
  if (lower.includes('cat')) return ['Follow the cat', 'Offer the cat food', 'Ask why the cat is watching'];
  return ['Investigate the strange clue', 'Ask who is watching', 'Leave quietly before it notices'];
}

export function formatStoryMarkdown({ title, premise, providerLabel, wordCount, chapterCount, sourceJobId, text }: {
  title: string;
  premise: string;
  providerLabel: string;
  wordCount: number;
  chapterCount: number;
  sourceJobId: string | null;
  text: string;
}): string {
  return [
    `# ${title}`,
    '',
    `- Provider: ${providerLabel}`,
    `- Words: ${wordCount}`,
    `- Chapters: ${chapterCount}`,
    sourceJobId ? `- Source job: ${sourceJobId}` : null,
    premise ? `- Premise: ${premise}` : null,
    '',
    '---',
    '',
    text,
    '',
  ].filter((line) => line !== null).join('\n');
}
