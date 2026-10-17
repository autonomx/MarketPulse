import { useState } from 'react';
import { loadStoryReadSettings, saveStoryReadSettings, type StoryReadSettings } from '../settings';

const presets = ['Dramatic audiobook', 'Calm bedtime', 'Documentary', 'Fast draft', 'Character-forward'];

export function StoryReadPanel() {
  const [settings, setSettings] = useState<StoryReadSettings>(() => loadStoryReadSettings());

  function update(next: StoryReadSettings): void {
    setSettings(next);
    saveStoryReadSettings(next);
  }

  return (
    <section className="storyteller-cast-panel" aria-label="Story read controls">
      <div className="storyteller-cast-heading">
        <div><p className="eyebrow">Read controls</p><h3>Pacing and style</h3></div>
        <strong>{settings.stylePreset}</strong>
      </div>
      <p>Control pacing, chapter title handling, speed, style preset, and word notes for future narration jobs.</p>
      <div className="storyteller-voice-cast-table">
        <article className="storyteller-voice-row"><div><strong>Style preset</strong><span>Global tone</span></div><select aria-label="Story read style" value={settings.stylePreset} onChange={(event) => update({ ...settings, stylePreset: event.currentTarget.value })}>{presets.map((preset) => <option key={preset} value={preset}>{preset}</option>)}</select><label><input type="checkbox" checked={settings.readChapterTitles} onChange={(event) => update({ ...settings, readChapterTitles: event.currentTarget.checked })} /> Read chapter titles</label></article>
        <article className="storyteller-voice-row"><div><strong>Speed</strong><span>{settings.speed.toFixed(2)}x</span></div><input aria-label="Story read speed" type="range" min="0.6" max="1.4" step="0.05" value={settings.speed} onChange={(event) => update({ ...settings, speed: Number(event.currentTarget.value) })} /><span /></article>
        <article className="storyteller-voice-row"><div><strong>Pauses</strong><span>Paragraph / chapter</span></div><input aria-label="Pause after paragraph" type="number" value={settings.pauseAfterParagraphMs} onChange={(event) => update({ ...settings, pauseAfterParagraphMs: Number(event.currentTarget.value) })} /><input aria-label="Pause after chapter" type="number" value={settings.pauseAfterChapterMs} onChange={(event) => update({ ...settings, pauseAfterChapterMs: Number(event.currentTarget.value) })} /></article>
        <article className="storyteller-cast-card"><div><strong>Word notes</strong><span>One note per line</span></div><textarea aria-label="Word notes" rows={4} value={settings.pronunciationDictionary} onChange={(event) => update({ ...settings, pronunciationDictionary: event.currentTarget.value })} /></article>
      </div>
    </section>
  );
}
