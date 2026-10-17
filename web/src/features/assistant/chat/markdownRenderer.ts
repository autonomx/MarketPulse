const RESEARCH_MODE_DEEP = 'deep';

type Metadata = Record<string, unknown> | undefined;
type CitationSource = { citation: string; title: string; url: string };

/**
 * Render the small, safe Markdown subset used by assistant and research replies.
 *
 * This deliberately escapes all source text and only permits http(s)/mailto links
 * so it can also be used by the DOM-based research progress controller.
 */
export function renderMarkdownHtml(markdown: string, metadata?: Metadata): string {
  const citations = citationSources(metadata);
  const lines = String(markdown ?? '').replaceAll('\r\n', '\n').replaceAll('\r', '\n').split('\n');
  const blocks: string[] = [];
  let index = 0;

  while (index < lines.length) {
    if (!lines[index].trim()) {
      index += 1;
      continue;
    }

    const fence = lines[index].match(/^\s{0,3}```\s*([\w+-]*)\s*$/);
    if (fence) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s{0,3}```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const language = fence[1] ? ` class="language-${escapeHtml(fence[1])}"` : '';
      blocks.push(`<pre><code${language}>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
      continue;
    }

    const heading = lines[index].match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(lines[index])) {
      blocks.push('<hr>');
      index += 1;
      continue;
    }

    const unordered = lines[index].match(/^\s{0,3}[-*+]\s+(.+)$/);
    const ordered = lines[index].match(/^\s{0,3}\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const orderedList = Boolean(ordered);
      const items: string[] = [];
      while (index < lines.length) {
        const item = lines[index].match(orderedList ? /^\s{0,3}\d+[.)]\s+(.+)$/ : /^\s{0,3}[-*+]\s+(.+)$/);
        if (!item) break;
        items.push(`<li>${renderInline(item[1])}</li>`);
        index += 1;
      }
      blocks.push(`<${orderedList ? 'ol' : 'ul'}>${items.join('')}</${orderedList ? 'ol' : 'ul'}>`);
      continue;
    }

    const quote = lines[index].match(/^\s{0,3}>\s?(.*)$/);
    if (quote) {
      const quoteLines: string[] = [];
      while (index < lines.length) {
        const quoteLine = lines[index].match(/^\s{0,3}>\s?(.*)$/);
        if (!quoteLine) break;
        quoteLines.push(quoteLine[1]);
        index += 1;
      }
      blocks.push(`<blockquote>${renderMarkdownHtml(quoteLines.join('\n'), metadata)}</blockquote>`);
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index].trim()) {
      if (paragraphLines.length > 0 && isBlockStart(lines[index])) break;
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    if (paragraphLines.length) {
      blocks.push(`<p>${paragraphLines.map((line) => renderInline(line, citations)).join('<br>')}</p>`);
    }
  }

  return blocks.join('');
}

export function isDeepResearchMessage(metadata: Metadata): boolean {
  return stringValue(metadata?.research_mode) === RESEARCH_MODE_DEEP;
}

export function renderAssistantMessageHtml(
  content: string,
  metadata: Metadata,
  messageId: string,
): string {
  const deepResearch = isDeepResearchMessage(metadata);
  const classes = `assistant-message-content${deepResearch ? ' assistant-research-report-host' : ''}`;
  const body = deepResearch ? renderResearchReportHtml(content, metadata) : renderMarkdownHtml(content, metadata);
  return `<div class="${classes}" data-omnix-message-content="true" data-raw-content="${escapeHtml(content)}" data-message-id="${escapeHtml(messageId)}">${body}</div>`;
}

export function renderResearchReportHtml(content: string, metadata: Metadata): string {
  const citations = countCitations(content);
  const pageLimit = firstNumber(metadata?.page_limit, asRecord(metadata?.research_budget).max_sources);
  const pagesReviewed = firstNumber(metadata?.extracted_pages);
  const searches = firstNumber(metadata?.logical_queries);
  const duration = formatDuration(firstNumber(metadata?.duration_ms, metadata?.research_duration_ms));
  const researchStatus = stringValue(metadata?.research_status);
  const partial = researchStatus === 'partial' || stringList(metadata?.research_warnings).length > 0;
  const meta = [
    'Research completed',
    duration ? `in ${duration}` : '',
    citations ? `${citations} citation${citations === 1 ? '' : 's'}` : '',
    searches !== null ? `${searches} search${searches === 1 ? '' : 'es'}` : '',
    pagesReviewed !== null && pageLimit !== null ? `${pagesReviewed}/${pageLimit} pages` : pageLimit !== null ? `${pageLimit} pages max` : '',
  ].filter(Boolean);

  return `<article class="assistant-research-report">
    <header class="assistant-research-report-header">
      <div class="assistant-research-report-heading">
        <span class="assistant-research-report-icon" aria-hidden="true">▤</span>
        <div><span class="assistant-research-report-kicker">Deep research</span><h3>Research report</h3></div>
      </div>
      <div class="assistant-research-report-tools">
        <button type="button" data-omnix-research-report-download aria-label="Download research report" title="Download report">⇩</button>
        <button type="button" data-omnix-research-report-expand aria-label="Expand research report" aria-pressed="false" title="Expand report">⛶</button>
      </div>
    </header>
    <div class="assistant-research-report-meta" aria-label="Research summary">
      <span>${escapeHtml(meta.join(' · '))}</span>
      ${partial ? '<span class="assistant-research-report-status">Limited evidence</span>' : '<span class="assistant-research-report-status complete">Complete</span>'}
    </div>
    <div class="assistant-research-report-body">${renderMarkdownHtml(content, metadata)}</div>
  </article>`;
}

function isBlockStart(line: string): boolean {
  return /^\s{0,3}(?:#{1,6}\s|```|[-*+]\s+|\d+[.)]\s+|>\s?)/.test(line)
    || /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line);
}

function renderInline(value: string, citations: Map<string, CitationSource> = new Map()): string {
  let output = '';
  let buffer = '';
  let index = 0;

  const flush = () => {
    if (buffer) {
      output += escapeHtml(buffer);
      buffer = '';
    }
  };

  while (index < value.length) {
    if (value[index] === '`') {
      const end = value.indexOf('`', index + 1);
      if (end > index + 1) {
        flush();
        output += `<code>${escapeHtml(value.slice(index + 1, end))}</code>`;
        index = end + 1;
        continue;
      }
    }

    const strongMarker = value.startsWith('**', index) ? '**' : value.startsWith('__', index) ? '__' : null;
    if (strongMarker) {
      const end = value.indexOf(strongMarker, index + 2);
      if (end > index + 2) {
        flush();
        output += `<strong>${renderInline(value.slice(index + 2, end), citations)}</strong>`;
        index = end + 2;
        continue;
      }
    }

    if (value[index] === '*' || value[index] === '_') {
      const marker = value[index];
      const end = value.indexOf(marker, index + 1);
      const openingWord = index > 0 && /[\w]/.test(value[index - 1]);
      if (!openingWord && end > index + 1) {
        flush();
        output += `<em>${renderInline(value.slice(index + 1, end), citations)}</em>`;
        index = end + 1;
        continue;
      }
    }

    if (value[index] === '[') {
      const closeBracket = value.indexOf(']', index + 1);
      if (closeBracket > index + 1) {
        const label = value.slice(index + 1, closeBracket);
        if (/^S\d+$/i.test(label)) {
          flush();
          const normalizedLabel = label.toUpperCase();
          const source = citations.get(normalizedLabel);
          if (source) {
            output += `<a class="assistant-research-citation" href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer" title="${escapeHtml(source.title ? `Open source: ${source.title}` : 'Open source')}" aria-label="Open source ${escapeHtml(normalizedLabel)}">[${escapeHtml(normalizedLabel)}]</a>`;
          } else {
            output += `<sup class="assistant-research-citation" title="Source citation">[${escapeHtml(normalizedLabel)}]</sup>`;
          }
          index = closeBracket + 1;
          continue;
        }
        if (value[closeBracket + 1] === '(') {
          const closeParen = value.indexOf(')', closeBracket + 2);
          if (closeParen > closeBracket + 2) {
            const href = safeHref(value.slice(closeBracket + 2, closeParen));
            if (href) {
              flush();
              output += `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${renderInline(label, citations)}</a>`;
              index = closeParen + 1;
              continue;
            }
          }
        }
      }
    }

    buffer += value[index];
    index += 1;
  }

  flush();
  return output;
}

function countCitations(content: string): number {
  return (content.match(/\[S\d+\]/gi) ?? []).length;
}

function citationSources(metadata: Metadata): Map<string, CitationSource> {
  const sources = Array.isArray(metadata?.context_sources)
    ? metadata.context_sources
    : Array.isArray(metadata?.research_sources) ? metadata.research_sources : [];
  const result = new Map<string, CitationSource>();
  let webSourceIndex = 0;
  for (const value of sources) {
    const source = asRecord(value);
    const sourceId = stringValue(source.source_id);
    const title = stringValue(source.title);
    const citation = stringValue(source.citation).toUpperCase()
      || title.match(/^\[(S\d+)\]/i)?.[1]?.toUpperCase()
      || (sourceId === 'web_search' ? `S${webSourceIndex + 1}` : '');
    if (sourceId === 'web_search') webSourceIndex += 1;
    const url = safeHref(stringValue(source.url));
    if (!citation || !url) continue;
    result.set(citation, { citation, title, url });
  }
  return result;
}

function safeHref(value: string): string | null {
  const href = value.trim();
  return /^(?:https?:|mailto:)/i.test(href) && !/[\s"'<>]/.test(href) ? href : null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : [];
}

function formatDuration(value: number | null): string | null {
  if (value === null || value < 0) return null;
  const seconds = Math.round(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m${seconds % 60 ? ` ${seconds % 60}s` : ''}`;
}

export function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
