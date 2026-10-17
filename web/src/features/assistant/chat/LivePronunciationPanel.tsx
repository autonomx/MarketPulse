import { useEffect, useState } from 'react';

import {
  livePronunciationClient,
  publishActivePronunciations,
  type PronunciationEntry,
} from './livePronunciationClient';

export function LivePronunciationPanel({ sessionId }: { sessionId: string | null }) {
  const [entries, setEntries] = useState<PronunciationEntry[]>([]);
  const [phrase, setPhrase] = useState('');
  const [pronunciation, setPronunciation] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setEntries([]);
    publishActivePronunciations([]);
    if (!sessionId) return () => { cancelled = true; };
    void livePronunciationClient.list(sessionId)
      .then((response) => {
        if (cancelled) return;
        setEntries(response.entries);
        publishActivePronunciations(response.entries);
      })
      .catch((error) => { if (!cancelled) setStatus(error instanceof Error ? error.message : 'Pronunciations could not be loaded.'); });
    return () => { cancelled = true; };
  }, [sessionId]);

  async function save(): Promise<void> {
    if (!sessionId || !phrase.trim() || !pronunciation.trim() || saving) return;
    setSaving(true);
    setStatus(null);
    try {
      const response = await livePronunciationClient.create(sessionId, phrase.trim(), pronunciation.trim());
      setEntries(response.entries);
      publishActivePronunciations(response.entries);
      setPhrase('');
      setPronunciation('');
      setStatus('Pronunciation saved for this live conversation.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Pronunciation could not be saved.');
    } finally {
      setSaving(false);
    }
  }

  async function remove(entryId: string): Promise<void> {
    if (!sessionId || saving) return;
    setSaving(true);
    try {
      const response = await livePronunciationClient.delete(sessionId, entryId);
      setEntries(response.entries);
      publishActivePronunciations(response.entries);
      setStatus('Pronunciation removed.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Pronunciation could not be removed.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="live-chat-card" aria-labelledby="live-chat-pronunciation-heading">
      <header><div><p className="eyebrow">Rendering continuity</p><h3 id="live-chat-pronunciation-heading">Pronunciations</h3><p>Keep names, places, acronyms, and preferred variants consistent across turns.</p></div><span className="live-chat-profile-source">{entries.length} saved</span></header>
      {!sessionId ? <p>Select a Chat session before saving pronunciation guidance.</p> : <>
        <div className="live-chat-control-grid live-chat-pronunciation-form">
          <label><span>Written phrase</span><input aria-label="Pronunciation phrase" value={phrase} placeholder="Nika" onChange={(event) => setPhrase(event.currentTarget.value)} /></label>
          <label><span>Say it as</span><input aria-label="Pronunciation rendering" value={pronunciation} placeholder="NEE-kah" onChange={(event) => setPronunciation(event.currentTarget.value)} /></label>
          <button type="button" disabled={saving || !phrase.trim() || !pronunciation.trim()} onClick={() => void save()}>Save pronunciation</button>
        </div>
        {entries.length ? <div className="live-chat-pronunciation-list">{entries.map((entry) => <div key={entry.id}><span><strong>{entry.phrase}</strong><small>{entry.pronunciation}</small></span><button type="button" disabled={saving} onClick={() => void remove(entry.id)}>Remove</button></div>)}</div> : <p className="live-chat-note">No session pronunciations yet.</p>}
      </>}
      {status ? <p className="live-chat-note" role="status">{status}</p> : null}
    </section>
  );
}
