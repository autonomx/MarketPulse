 
import { useEffect, useMemo, useState } from 'react';

import {
  liveChatEvaluationClient,
  suggestPresencePolicy,
  type LiveChatReleaseGateReport,
  type PresencePolicyVersion,
  type PresencePreset,
  type VoiceSessionEvaluationRecord,
} from '../workspace/live-chat-evaluation-client';
import './VoiceSessionEvaluationPanel.css';
import { downloadJson } from '../../../shared/download';
import { LIVE_CONVERSATION_DURABLE_EVALUATION_SAVED_EVENT, LIVE_PRESENCE_POLICY_REFRESH_EVENT } from '../../../events/bus';

const PRESETS: PresencePreset[] = ['quiet', 'natural', 'engaged', 'listener'];
const MINIMUM_TUNING_EVIDENCE = 5;

export function VoiceSessionEvaluationPanel() {
  const [evaluations, setEvaluations] = useState<VoiceSessionEvaluationRecord[]>([]);
  const [gate, setGate] = useState<LiveChatReleaseGateReport | null>(null);
  const [policies, setPolicies] = useState<Record<PresencePreset, PresencePolicyVersion> | null>(null);
  const [versions, setVersions] = useState<PresencePolicyVersion[]>([]);
  const [selectedPreset, setSelectedPreset] = useState<PresencePreset>('natural');
  const [candidate, setCandidate] = useState<PresencePolicyVersion | null>(null);
  const [status, setStatus] = useState<string>('Loading Voice Session evidence…');
  const [busy, setBusy] = useState(false);

  async function refresh(): Promise<void> {
    try {
      const nextGate = await liveChatEvaluationClient.releaseGate({ persistStatus: true });
      const [nextEvaluations, nextPolicies, nextVersions] = await Promise.all([
        liveChatEvaluationClient.list({ limit: 100 }),
        liveChatEvaluationClient.activePolicies(),
        liveChatEvaluationClient.policyVersions(),
      ]);
      setGate(nextGate);
      setEvaluations(nextEvaluations);
      setPolicies(nextPolicies);
      setVersions(nextVersions);
      setStatus(nextEvaluations.length
        ? `${nextEvaluations.length} durable Voice Session evaluation${nextEvaluations.length === 1 ? '' : 's'} loaded.`
        : 'No durable Voice Session evaluations yet. Complete a live call to create one.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Voice Session evidence could not be loaded.');
    }
  }

  useEffect(() => {
    void refresh();
    const handleSaved = () => void refresh();
    window.addEventListener(LIVE_CONVERSATION_DURABLE_EVALUATION_SAVED_EVENT, handleSaved);
    return () => window.removeEventListener(LIVE_CONVERSATION_DURABLE_EVALUATION_SAVED_EVENT, handleSaved);
  }, []);

  const selectedEvidence = useMemo(
    () => evaluations.filter((record) => record.presence_preset === selectedPreset),
    [evaluations, selectedPreset],
  );
  const activePolicy = policies?.[selectedPreset] ?? null;
  const policyVersions = versions.filter((version) => version.preset === selectedPreset);
  const latestMetrics = aggregateMetrics(selectedEvidence);

  async function createTuningCandidate(): Promise<void> {
    if (!activePolicy || busy) return;
    if (selectedEvidence.length < MINIMUM_TUNING_EVIDENCE) {
      setStatus(`At least ${MINIMUM_TUNING_EVIDENCE} ${selectedPreset} evaluations are required before tuning.`);
      return;
    }
    setBusy(true);
    try {
      const created = await liveChatEvaluationClient.createPolicyVersion(selectedPreset, {
        values: suggestPresencePolicy(activePolicy, selectedEvidence),
        reason: 'evidence_driven_voice_session_tuning',
        evidence_evaluation_ids: selectedEvidence.slice(0, 200).map((record) => record.evaluation_id),
      });
      setCandidate(created);
      setVersions((current) => [...current, created]);
      setStatus(`${title(selectedPreset)} policy v${created.version} created as an inactive tuning candidate.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Tuning candidate could not be created.');
    } finally {
      setBusy(false);
    }
  }

  async function activateCandidate(): Promise<void> {
    if (!candidate || busy) return;
    setBusy(true);
    try {
      const activated = await liveChatEvaluationClient.activatePolicy(candidate.preset, candidate.version);
      setPolicies((current) => current ? { ...current, [activated.preset]: activated } : current);
      setCandidate(null);
      await refresh();
      window.dispatchEvent(new Event(LIVE_PRESENCE_POLICY_REFRESH_EVENT));
      setStatus(`${title(activated.preset)} policy v${activated.version} is active. Explicit user overrides were not changed.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Tuning candidate could not be activated.');
    } finally {
      setBusy(false);
    }
  }

  async function rollback(): Promise<void> {
    if (!activePolicy || activePolicy.version <= 1 || busy) return;
    setBusy(true);
    try {
      const rolledBack = await liveChatEvaluationClient.rollbackPolicy(selectedPreset);
      setPolicies((current) => current ? { ...current, [rolledBack.preset]: rolledBack } : current);
      await refresh();
      window.dispatchEvent(new Event(LIVE_PRESENCE_POLICY_REFRESH_EVENT));
      setStatus(`${title(rolledBack.preset)} rolled back to policy v${rolledBack.version}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Presence policy could not be rolled back.');
    } finally {
      setBusy(false);
    }
  }

  async function exportEvidence(): Promise<void> {
    setBusy(true);
    try {
      const payload = await liveChatEvaluationClient.export();
      downloadJson(payload, 'omnix-live-chat-evidence.json');
      setStatus('Content-free Voice Session evidence exported.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Voice Session evidence export failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="voice-session-evaluation" aria-labelledby="voice-session-quality-heading">
      <header>
        <div>
          <p className="eyebrow">Conversation evidence</p>
          <h3 id="voice-session-quality-heading">Voice Session quality</h3>
          <p>Compare content-free call metrics across characters, presence presets, and calibrated environments.</p>
        </div>
        <button type="button" disabled={busy || !evaluations.length} onClick={() => void exportEvidence()}>Export evidence</button>
      </header>

      <div className="voice-session-summary-grid">
        <Metric label="Sessions" value={String(evaluations.length)} />
        <Metric label="First audio p95" value={formatMilliseconds(latestMetrics.firstAudioP95)} />
        <Metric label="Interruption success" value={formatPercent(latestMetrics.interruptionSuccess)} />
        <Metric label="Listening score" value={formatScore(latestMetrics.listeningScore)} />
        <Metric label="Pressure score" value={formatScore(latestMetrics.pressureScore)} />
        <Metric label="Release evidence" value={gate ? title(gate.status) : 'Loading'} />
      </div>

      <section className="voice-session-gate" aria-labelledby="voice-session-gate-heading">
        <h4 id="voice-session-gate-heading">Aggregate release gate</h4>
        <p>
          {gate
            ? `${gate.scenarios.length} scenarios observed · ${gate.traces} durable traces · ${gate.character_modes.join(' + ') || 'identity evidence missing'}`
            : 'Evaluating durable release evidence…'}
        </p>
        {gate?.failures.length ? <p role="alert">Failures: {gate.failures.slice(0, 3).join('; ')}</p> : null}
        {gate?.missing_scenarios.length ? (
          <p>Still required: {gate.missing_scenarios.slice(0, 6).map(title).join(', ')}{gate.missing_scenarios.length > 6 ? ` +${gate.missing_scenarios.length - 6} more` : ''}.</p>
        ) : null}
      </section>

      <section className="voice-session-policy" aria-labelledby="voice-session-policy-heading">
        <header>
          <div>
            <h4 id="voice-session-policy-heading">Versioned presence policy</h4>
            <p>Tuning creates an inactive candidate first. Activation and rollback are explicit and never overwrite session overrides.</p>
          </div>
          <label>
            <span>Preset</span>
            <select aria-label="Presence policy preset" value={selectedPreset} onChange={(event) => { setSelectedPreset(event.currentTarget.value as PresencePreset); setCandidate(null); }}>
              {PRESETS.map((preset) => <option key={preset} value={preset}>{title(preset)}</option>)}
            </select>
          </label>
        </header>
        <dl>
          <div><dt>Active version</dt><dd>{activePolicy ? `v${activePolicy.version}` : 'Unavailable'}</dd></div>
          <div><dt>Evidence</dt><dd>{selectedEvidence.length} sessions</dd></div>
          <div><dt>Silence tolerance</dt><dd>{activePolicy ? formatMilliseconds(activePolicy.values.silence_tolerance_ms) : '—'}</dd></div>
          <div><dt>Initiative threshold</dt><dd>{activePolicy ? formatMilliseconds(activePolicy.values.initiative_threshold_ms) : '—'}</dd></div>
          <div><dt>Backchannel frequency</dt><dd>{activePolicy ? formatPercent(activePolicy.values.listener_backchannel_frequency) : '—'}</dd></div>
          <div><dt>Known versions</dt><dd>{policyVersions.length}</dd></div>
        </dl>
        <div className="voice-session-policy-actions">
          <button type="button" disabled={busy || !activePolicy} onClick={() => void createTuningCandidate()}>Create tuning candidate</button>
          <button type="button" disabled={busy || !candidate} onClick={() => void activateCandidate()}>{candidate ? `Activate v${candidate.version}` : 'Activate candidate'}</button>
          <button type="button" disabled={busy || !activePolicy || activePolicy.version <= 1} onClick={() => void rollback()}>Roll back active policy</button>
        </div>
      </section>

      <div className="voice-session-records" role="list" aria-label="Durable Voice Session evaluations">
        {evaluations.slice(0, 20).map((record) => (
          <article key={record.evaluation_id} role="listitem">
            <header><strong>{record.character_id === 'system-assistant' ? 'System Assistant' : record.character_id}</strong><time dateTime={record.ended_at}>{new Date(record.ended_at).toLocaleString()}</time></header>
            <p>{title(record.presence_preset)} · {record.resolved_duplex_mode === 'echo_aware' ? 'Echo-aware' : 'Safe half-duplex'} · policy/profile v{record.profile_version ?? '—'}</p>
            <dl>
              <div><dt>First audio p95</dt><dd>{formatMilliseconds(record.latency_summary.first_audio_p95_ms)}</dd></div>
              <div><dt>Interruptions</dt><dd>{formatPercent(record.quality_metrics.interruption_success_rate)}</dd></div>
              <div><dt>Natural EOS</dt><dd>{record.eos_termination_counts.natural_eos ?? 0}</dd></div>
              <div><dt>Gate</dt><dd>{title(record.release_gate_status)}</dd></div>
            </dl>
          </article>
        ))}
        {!evaluations.length ? <p className="muted">No durable evaluations have been recorded.</p> : null}
      </div>
      <p role="status" className="voice-session-status">{status}</p>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function aggregateMetrics(records: VoiceSessionEvaluationRecord[]) {
  return {
    firstAudioP95: average(records.map((record) => record.latency_summary.first_audio_p95_ms)),
    interruptionSuccess: average(records.map((record) => record.quality_metrics.interruption_success_rate)),
    listeningScore: average(records.map((record) => record.listening_score)),
    pressureScore: average(records.map((record) => record.pressure_score)),
  };
}

function average(values: Array<number | null | undefined>): number | null {
  const numeric = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  return numeric.length ? numeric.reduce((sum, value) => sum + value, 0) / numeric.length : null;
}

function formatMilliseconds(value: number | null | undefined): string {
  return typeof value === 'number' ? `${Math.round(value)} ms` : '—';
}

function formatPercent(value: number | null | undefined): string {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '—';
}

function formatScore(value: number | null | undefined): string {
  return typeof value === 'number' ? `${value.toFixed(1)} / 5` : '—';
}

function title(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (character) => character.toLocaleUpperCase());
}
