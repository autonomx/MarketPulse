import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/**
 * The audiobook stylesheet is written for the dark palette, which does not flip in
 * light appearance. Every rule that paints a dark surface or light text therefore
 * needs a light-appearance rule, unless the element keeps its colours on purpose.
 */
const LIGHT_PREFIX = /:root\[data-omnix-appearance='light'\]\s*/g;

// Elements whose colours are their content: covers, artwork, gradient buttons,
// coloured badges and the modal backdrop look the same in either appearance.
const KEEPS_OWN_COLOURS = new Set([
  '.asset-audio', '.asset-document', '.asset-export', '.asset-preview-type', '.audiobook-asset-preview',
  '.audiobook-book-card > img', '.audiobook-card-actions button:first-child', '.audiobook-chapter-grid-cover',
  '.audiobook-chapter-placeholder', '.audiobook-create-details-actions button', '.audiobook-danger-action:hover:not(:disabled)',
  '.audiobook-ebook-cover', '.audiobook-ebook-list-cover', '.audiobook-file-button',
  '.audiobook-header-action-buttons .audiobook-primary-action', '.audiobook-hero-art', '.audiobook-library .audiobook-form button',
  '.audiobook-library-books i', '.audiobook-library-books i:nth-child(1)', '.audiobook-library-books i:nth-child(2)',
  '.audiobook-library-books i:nth-child(3)', '.audiobook-library-books i:nth-child(4)', '.audiobook-library-books i:nth-child(5)',
  '.audiobook-library-hero-art', '.audiobook-library-nav button.selected', '.audiobook-library-nav button:hover:not(:disabled)',
  '.audiobook-library-selected-cover', '.audiobook-modal-backdrop', '.audiobook-primary-action',
  '.audiobook-production .audiobook-action-row button:last-child', '.audiobook-source-file-button', '.audiobook-voice-icon',
]);

/** A palette token's lightness step: --c-teal-860-2 is 860 (high is light). */
const steps = (value: string) => [...value.matchAll(/--c-[a-z]+-(\d+)/g)].map((match) => Number(match[1]));

const normalize = (selector: string) => selector
  .replace(LIGHT_PREFIX, '')
  .replace(/:not\([^)]*\)/g, '')
  .replace(/^\.audiobook-workspace\s+(?=\S)/, '')
  .trim();

function rules(css: string): Array<[string[], string]> {
  const plain = css.replace(/\/\*[\s\S]*?\*\//g, '');
  return [...plain.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selector, body]) => [
    // Drop any at-rule or layer text that precedes the selector in the match.
    selector.split(/[;{}]/).pop()!.split(',').map((part) => part.trim()).filter(Boolean),
    body,
  ]);
}

describe('audiobook light appearance', () => {
  it('gives every dark-only surface and light text colour a light-appearance rule', () => {
    const parsed = rules(readFileSync('src/features/audiobook/AudiobookWorkspace.css', 'utf8'));
    const lightSelectors = new Set(parsed
      .flatMap(([selectors]) => selectors)
      .filter((selector) => selector.includes("data-omnix-appearance='light'"))
      .map(normalize));

    const uncovered = new Set<string>();
    for (const [selectors, body] of parsed) {
      if (selectors.some((selector) => selector.includes('data-omnix-appearance'))) continue;
      const darkOnly = body.split(';').some((declaration) => {
        const [property, ...rest] = declaration.split(':');
        const values = steps(rest.join(':'));
        if (!values.length) return false;
        const name = property.trim();
        return ((name === 'background' || name === 'background-color') && Math.min(...values) < 400)
          || (name === 'color' && Math.max(...values) >= 700);
      });
      if (!darkOnly) continue;
      for (const selector of selectors) {
        if (!lightSelectors.has(normalize(selector)) && !KEEPS_OWN_COLOURS.has(selector)) uncovered.add(selector);
      }
    }
    expect([...uncovered].sort()).toEqual([]);
  });
});
