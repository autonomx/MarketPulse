import { describe, expect, it } from 'vitest';
import { renderAssistantMessageHtml, renderMarkdownHtml, renderResearchReportHtml } from './markdownRenderer';

describe('assistant Markdown rendering', () => {
  it('renders report-friendly blocks and inline emphasis', () => {
    const html = renderMarkdownHtml(
      '# GameStop outlook\n\n**Recommendation:** Hold for now.\n\n- Avoid leverage\n- Stage entries\n\nSee [the filing](https://example.com/filing) [S1].',
    );

    expect(html).toContain('<h1>GameStop outlook</h1>');
    expect(html).toContain('<strong>Recommendation:</strong> Hold for now.');
    expect(html).toContain('<ul><li>Avoid leverage</li><li>Stage entries</li></ul>');
    expect(html).toContain('href="https://example.com/filing"');
    expect(html).toContain('assistant-research-citation');
  });

  it('escapes HTML and rejects unsafe links', () => {
    const html = renderMarkdownHtml('<script>alert(1)</script> [bad](javascript:alert(1))');

    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('href="javascript:');
  });

  it('turns retrieved source citations into safe clickable links', () => {
    const html = renderAssistantMessageHtml(
      'The current conditions are mild. [S1] [S2]',
      {
        context_sources: [
          { source_id: 'web_search', citation: 'S1', title: 'Weather source', url: 'https://example.com/weather' },
          { source_id: 'web_search', citation: 'S2', title: 'Unsafe source', url: 'javascript:alert(1)' },
        ],
      },
      'msg:weather',
    );

    expect(html).toContain('class="assistant-research-citation" href="https://example.com/weather"');
    expect(html).toContain('aria-label="Open source S1"');
    expect(html).toContain('<sup class="assistant-research-citation" title="Source citation">[S2]</sup>');
    expect(html).not.toContain('href="javascript:');
  });

  it('adds research metadata and preserves a five-page budget in the report header', () => {
    const html = renderResearchReportHtml('**Inference:** A volatile setup. [S1] [S2]', {
      research_mode: 'deep',
      research_status: 'partial',
      logical_queries: 1,
      extracted_pages: 0,
      research_budget: { max_sources: 5 },
      research_warnings: ['page_extraction_partial'],
    });

    expect(html).toContain('Deep research');
    expect(html).toContain('2 citations');
    expect(html).toContain('1 search');
    expect(html).toContain('0/5 pages');
    expect(html).toContain('Limited evidence');
    expect(html).toContain('<strong>Inference:</strong>');
    expect(html).toContain('data-omnix-research-report-download');
    expect(html).toContain('data-omnix-research-report-expand');
  });

  it('creates a shared message wrapper for DOM-injected research replies', () => {
    const html = renderAssistantMessageHtml('# Result', { research_mode: 'deep' }, 'msg:1');

    expect(html).toContain('data-omnix-message-content="true"');
    expect(html).toContain('data-raw-content="# Result"');
    expect(html).toContain('assistant-research-report');
  });
});
