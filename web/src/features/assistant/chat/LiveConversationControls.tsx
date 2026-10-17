import { useEffect, useState } from 'react';

import {
  type AssistantBackchannelMode,
  type ConversationPace,
  type ConversationStance,
  type DuplexMode,
  type InitiativeMode,
  type InterruptionPreference,
  type LiveConversationProfile,
  type LiveConversationProfilePatch,
  type LongPauseBehavior,
  type EmotionalAttunement,
  type PronunciationSavePolicy,
  type PresencePreset,
  type ResponseLength,
  type ResponseOnsetStyle,
  type TopicContinuity,
  liveConversationProfileClient,
  migrateLegacyConversationSettingsOnce,
  mirrorProfileForLegacyRuntime,
} from './liveConversationProfileClient';
import {
  LIVE_CONVERSATION_MODE_PROFILES,
  matchLiveConversationModeProfile,
} from './liveConversationModeProfiles';

export type LiveConversationControlsProps = { sessionId: string | null };

export function LiveConversationControls({ sessionId }: LiveConversationControlsProps) {
  const [profile, setProfile] = useState<LiveConversationProfile | null>(null);
  const [source, setSource] = useState<'user_defaults' | 'session_override'>('user_defaults');
  const [advanced, setAdvanced] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setProfile(null);
    setStatus(null);
    void (async () => {
      try {
        const migration = migrateLegacyConversationSettingsOnce().catch(() => false);
        if (sessionId) {
          const envelope = await liveConversationProfileClient.get(sessionId);
          if (cancelled) return;
          setProfile(envelope.effective);
          setSource(envelope.source);
          mirrorProfileForLegacyRuntime(envelope.effective);
        } else {
          const defaults = await liveConversationProfileClient.defaults();
          if (cancelled) return;
          setProfile(defaults);
          setSource('user_defaults');
          mirrorProfileForLegacyRuntime(defaults);
        }
        // Migration is best-effort and must never block the visible controls. If it
        // changes defaults, refresh the effective profile once it completes.
        void migration.then(async (migrated) => {
          if (!migrated || cancelled) return;
          try {
            if (sessionId) {
              const envelope = await liveConversationProfileClient.get(sessionId);
              if (cancelled) return;
              setProfile(envelope.effective);
              setSource(envelope.source);
              mirrorProfileForLegacyRuntime(envelope.effective);
            } else {
              const defaults = await liveConversationProfileClient.defaults();
              if (cancelled) return;
              setProfile(defaults);
              setSource('user_defaults');
              mirrorProfileForLegacyRuntime(defaults);
            }
          } catch {
            // Keep the already loaded profile when the optional refresh fails.
          }
        });
      } catch (error) {
        if (!cancelled) setStatus(error instanceof Error ? error.message : 'Live Chat profile could not be loaded.');
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId]);

  async function update(patch: LiveConversationProfilePatch): Promise<void> {
    if (!profile || saving) return;
    setSaving(true);
    setStatus(null);
    try {
      if (sessionId) {
        const envelope = await liveConversationProfileClient.update(sessionId, patch);
        setProfile(envelope.effective);
        setSource(envelope.source);
        mirrorProfileForLegacyRuntime(envelope.effective);
      } else {
        const defaults = await liveConversationProfileClient.updateDefaults(patch);
        setProfile(defaults);
        setSource('user_defaults');
        mirrorProfileForLegacyRuntime(defaults);
      }
      setStatus(sessionId ? 'Session presence profile saved.' : 'Default presence profile saved.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Live Chat profile could not be saved.');
    } finally {
      setSaving(false);
    }
  }

  async function clearOverride(): Promise<void> {
    if (!sessionId || saving) return;
    setSaving(true);
    setStatus(null);
    try {
      const envelope = await liveConversationProfileClient.clear(sessionId);
      setProfile(envelope.effective);
      setSource(envelope.source);
      mirrorProfileForLegacyRuntime(envelope.effective);
      setStatus('This session now uses your Live Chat defaults.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Session override could not be cleared.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="live-chat-card" aria-labelledby="live-chat-presence-heading">
      <header>
        <div><p className="eyebrow">Conversation presence</p><h3 id="live-chat-presence-heading">Presence and stance</h3><p>Choose how active the character feels without changing who the character is.</p></div>
        <span className="live-chat-profile-source">{sessionId && source === 'session_override' ? 'Session override' : 'User defaults'}</span>
      </header>

      {!profile ? <p role="status">{status ?? 'Loading Live Chat profile…'}</p> : <>
        <div role="group" className="live-chat-mode-profiles" aria-label="Conversation profiles">
          <div className="live-chat-mode-profile-heading">
            <div>
              <strong>Conversation profile</strong>
              <span>Apply a complete, compatible set of presence and turn-taking options.</span>
              <small>{matchLiveConversationModeProfile(profile) ? 'Preset active' : 'Custom configuration'}</small>
            </div>
          </div>
          <div className="live-chat-mode-profile-grid">
            {LIVE_CONVERSATION_MODE_PROFILES.map((modeProfile) => {
              const active = matchLiveConversationModeProfile(profile) === modeProfile.id;
              return (
                <button
                  className="live-chat-mode-profile"
                  type="button"
                  key={modeProfile.id}
                  aria-pressed={active}
                  disabled={saving}
                  onClick={() => void update({ ...modeProfile.settings })}
                >
                  <strong>{modeProfile.label}</strong>
                  <span>{modeProfile.description}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="live-chat-control-grid">
          <SelectControl label="Presence" value={profile.presence_preset} disabled={saving} onChange={(value) => void update({ presence_preset: value as PresencePreset })} options={[
            ['quiet', 'Quiet'], ['natural', 'Natural'], ['engaged', 'Engaged'], ['listener', 'Listener'],
          ]} />
          <SelectControl label="Conversation stance" value={profile.conversation_stance} disabled={saving} onChange={(value) => void update({ conversation_stance: value as ConversationStance })} options={[
            ['automatic', 'Automatic'], ['listen', 'Listen'], ['discuss', 'Discuss'], ['advise', 'Advise'], ['brainstorm', 'Brainstorm'], ['teach', 'Teach'],
          ]} />
          <SelectControl label="Response length" value={profile.response_length} disabled={saving} onChange={(value) => void update({ response_length: value as ResponseLength })} options={[
            ['brief', 'Brief'], ['conversational', 'Conversational'], ['detailed', 'Detailed'],
          ]} />
          <label><span>Talkativeness</span><input aria-label="Talkativeness" type="range" min="0" max="100" step="5" value={profile.talkativeness} disabled={saving} onChange={(event) => void update({ talkativeness: Number(event.currentTarget.value) })} /><strong>{profile.talkativeness < 35 ? 'Less talkative' : profile.talkativeness > 65 ? 'More talkative' : 'Balanced'}</strong></label>
        </div>

        <button className="live-chat-advanced-toggle" type="button" aria-expanded={advanced} onClick={() => setAdvanced((value) => !value)}>{advanced ? 'Hide advanced controls' : 'Show advanced controls'}</button>

        {advanced ? <div className="live-chat-control-grid live-chat-advanced-controls">
          <SelectControl label="Conversation pace" value={profile.conversation_pace} disabled={saving} onChange={(value) => void update({ conversation_pace: value as ConversationPace })} options={[
            ['quick', 'Quick'], ['balanced', 'Balanced'], ['reflective', 'Reflective'],
          ]} />
          <SelectControl label="Interruption behavior" value={profile.interruption_preference} disabled={saving} onChange={(value) => void update({ interruption_preference: value as InterruptionPreference })} options={[
            ['easy', 'Easy to interrupt'], ['balanced', 'Balanced'], ['finish_more', 'Finish more often'],
          ]} />
          <SelectControl label="Assistant listener backchannels" value={profile.assistant_backchannel_mode} disabled={saving} onChange={(value) => void update({ assistant_backchannel_mode: value as AssistantBackchannelMode })} options={[
            ['off', 'Off'], ['minimal', 'Minimal'], ['natural', 'Natural'],
          ]} />
          <SelectControl label="Duplex mode" value={profile.duplex_mode} disabled={saving} onChange={(value) => void update({ duplex_mode: value as DuplexMode })} options={[
            ['automatic', 'Automatic (safe fallback)'], ['half_duplex', 'Safe half-duplex'], ['echo_aware', 'Echo-aware barge-in'],
          ]} />
          <SelectControl label="Character initiative" value={profile.initiative_mode} disabled={saving} onChange={(value) => void update({ initiative_mode: value as InitiativeMode })} options={[
            ['off', 'Off'], ['gentle', 'Gentle'], ['active', 'Active'],
          ]} />
          <SelectControl label="Long-pause behavior" value={profile.long_pause_behavior} disabled={saving} onChange={(value) => void update({ long_pause_behavior: value as LongPauseBehavior })} options={[
            ['wait', 'Wait silently'], ['reassure', 'Gentle reassurance'], ['ask_to_continue', 'Ask whether to continue'],
          ]} />
          <SelectControl label="Response onset" value={profile.response_onset_style} disabled={saving} onChange={(value) => void update({ response_onset_style: value as ResponseOnsetStyle })} options={[
            ['adaptive', 'Adaptive'], ['immediate', 'Immediate'], ['natural', 'Natural pause'], ['reflective', 'Reflective pause'],
          ]} />
          <SelectControl label="Emotional attunement" value={profile.emotional_attunement} disabled={saving} onChange={(value) => void update({ emotional_attunement: value as EmotionalAttunement })} options={[
            ['off', 'Off'], ['subtle', 'Subtle'], ['expressive', 'Expressive'],
          ]} />
          <SelectControl label="Topic continuity" value={profile.topic_continuity} disabled={saving} onChange={(value) => void update({ topic_continuity: value as TopicContinuity })} options={[
            ['focused', 'Focused'], ['natural', 'Natural'], ['exploratory', 'Exploratory'],
          ]} />
          <SelectControl label="Pronunciation saving" value={profile.pronunciation_save_policy} disabled={saving} onChange={(value) => void update({ pronunciation_save_policy: value as PronunciationSavePolicy })} options={[
            ['ask', 'Ask before saving'], ['session_only', 'This session only'], ['allow', 'Allow saving'],
          ]} />
          <label><span>First idle prompt</span><input aria-label="First idle prompt seconds" type="number" min="1" max="120" step="1" value={Math.round(profile.idle_threshold_ms / 1000)} disabled={saving} onChange={(event) => void update({ idle_threshold_ms: Number(event.currentTarget.value) * 1000 })} /><strong>seconds</strong></label>
          <label><span>Maximum prompts per quiet period</span><input aria-label="Maximum prompts per quiet period" type="number" min="0" max="3" step="1" value={profile.max_idle_prompts} disabled={saving} onChange={(event) => void update({ max_idle_prompts: Number(event.currentTarget.value) })} /></label>
        </div> : null}

        <div className="live-chat-profile-actions">{sessionId && source === 'session_override' ? <button type="button" disabled={saving} onClick={() => void clearOverride()}>Use my defaults</button> : null}<small>Profile version {profile.profile_version}</small></div>
        {status ? <p className="live-chat-note" role="status">{status}</p> : null}
      </>}
    </section>
  );
}

function SelectControl({ label, value, options, disabled, onChange }: { label: string; value: string; options: Array<[string, string]>; disabled: boolean; onChange: (value: string) => void }) {
  return <label><span>{label}</span><select aria-label={label} value={value} disabled={disabled} onChange={(event) => onChange(event.currentTarget.value)}>{options.map(([optionValue, display]) => <option key={optionValue} value={optionValue}>{display}</option>)}</select></label>;
}
