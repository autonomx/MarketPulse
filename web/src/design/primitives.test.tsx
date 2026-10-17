import { readFileSync } from 'node:fs';
import { cleanup, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { OmnixAssetCard, OmnixAudioControls, OmnixDiagnosticsView, OmnixProgressLog, OmnixTopBar, OmnixTranscriptView } from './primitives';
import { renderWithProviders } from '../test/renderWithProviders';
import { unlayeredCss } from '../test/unlayeredCss';

const styles = unlayeredCss(readFileSync('src/styles.css', 'utf8'));
const themePresets = readFileSync('src/theme-presets.css', 'utf8');
const styleElement = document.createElement('style');
document.head.appendChild(styleElement);



describe('design primitives', () => {
  it.each([
    ['dark', '#f4f7ff'],
    ['light', '#172033'],
  ])('keeps the Assistant header label readable in %s mode', (appearance, textColor) => {
    document.documentElement.dataset.omnixAppearance = appearance;
    styleElement.textContent = styles.replaceAll('var(--omnix-text)', textColor);
    renderWithProviders(<OmnixTopBar title="Chatbot" />);

    const assistantLabel = document.querySelector('.omnix-topbar-status span');
    expect(assistantLabel).toHaveTextContent('Assistant');
    expect(assistantLabel).toBeVisible();
    expect(styles).toContain('.omnix-topbar-status span { color: var(--omnix-text);');
    expect(getComputedStyle(assistantLabel as HTMLElement).color).toBe(appearance === 'dark' ? 'rgb(244, 247, 255)' : 'rgb(23, 32, 51)');

    cleanup();
    delete document.documentElement.dataset.omnixAppearance;
  });

  it('removes the legacy graphite light toggle dot treatment', () => {
    const graphiteToggleStart = ":root[data-omnix-theme='graphite'][data-omnix-appearance='light'] .omnix-shell-toggle {";
    const graphiteToggleStartIndex = themePresets.indexOf(graphiteToggleStart);
    const graphiteToggleRule = themePresets.slice(graphiteToggleStartIndex, themePresets.indexOf('}', graphiteToggleStartIndex));

    expect(graphiteToggleRule).toBeDefined();
    expect(graphiteToggleRule).toContain('linear-gradient');
    expect(graphiteToggleRule).not.toContain('radial-gradient');
  });

  it('shows a directional arrow for the sidebar toggle state', () => {
    const { rerender } = renderWithProviders(<OmnixTopBar title="Chatbot" isSidebarVisible />);

    expect(document.querySelector('.omnix-shell-toggle span')).toHaveTextContent('‹');
    expect(document.querySelector('.omnix-shell-toggle span')).toHaveAttribute('data-direction', 'left');

    rerender(<OmnixTopBar title="Chatbot" isSidebarVisible={false} />);
    expect(document.querySelector('.omnix-shell-toggle span')).toHaveTextContent('›');
    expect(document.querySelector('.omnix-shell-toggle span')).toHaveAttribute('data-direction', 'right');
    cleanup();
  });

  it('renders shared operational primitives', () => {
    renderWithProviders(
      <>
        <OmnixProgressLog value={50} logs={[{ level: 'info', message: 'Halfway' }]} />
        <OmnixTranscriptView messages={[{ role: 'assistant', content: 'Ready.' }]} />
        <OmnixAudioControls label="preview" />
        <OmnixAssetCard title="Image asset" metadata="image/png" />
        <OmnixDiagnosticsView rows={[{ label: 'Gateway', value: 'ready' }]} />
      </>,
    );

    expect(screen.getByLabelText('Progress')).toBeInTheDocument();
    expect(screen.getByText('Halfway')).toBeInTheDocument();
    expect(screen.getByText('Ready.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Play preview' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Image asset' })).toBeInTheDocument();
    expect(screen.getByText('Gateway')).toBeInTheDocument();
    expect(screen.getByText('ready')).toBeInTheDocument();
  });
});
