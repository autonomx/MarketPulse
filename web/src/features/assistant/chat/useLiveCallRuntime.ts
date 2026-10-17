import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { characterClient, type CharacterLiveCallRuntime } from './characterClient';
import type { ChatSessionListEntry } from './useChatSessions';
import { CHARACTER_AVATAR_RUNTIME_EVENT } from '../../../events/bus';

type LiveCallRuntimeOptions = {
  selectedSessionId: string | null;
  selectedSessionSummary: ChatSessionListEntry | undefined;
  interaction: Awaited<ReturnType<typeof characterClient.session>> | undefined;
  /** While a call runs, its runtime stays as the call started it. */
  liveVoiceActiveRef: RefObject<boolean>;
};

/** The live-call runtime (identity, voice, avatar, memory) of the selected session, preloaded in Character mode. */
export function useLiveCallRuntime({ selectedSessionId, selectedSessionSummary, interaction, liveVoiceActiveRef }: LiveCallRuntimeOptions) {
  const [liveCallRuntime, setLiveCallRuntime] = useState<CharacterLiveCallRuntime | null>(null);
  const liveCallRuntimeRef = useRef<CharacterLiveCallRuntime | null>(null);
  const liveCallRuntimeQuery = useQuery({
    queryKey: [
      'feature',
      'chatbot',
      'live-call-runtime',
      selectedSessionId,
      interaction?.interaction_mode,
      interaction?.character_id,
      interaction?.character_profile_version,
    ],
    queryFn: () => characterClient.liveCallRuntime(selectedSessionId ?? ''),
    // Runtime preload includes the selected character's avatar/voice/memory.
    // Keep it out of ordinary system chats, but make it available in
    // Character mode so the right rail reflects the selected identity before
    // a call starts.
    enabled: Boolean(
      selectedSessionId
      && selectedSessionSummary?.interaction_mode === 'character'
      && interaction?.interaction_mode === 'character',
    ),
  });

  useEffect(() => {
    if (!liveCallRuntimeQuery.data || liveVoiceActiveRef.current) return;
    liveCallRuntimeRef.current = liveCallRuntimeQuery.data;
    setLiveCallRuntime(liveCallRuntimeQuery.data);
  }, [liveCallRuntimeQuery.data, liveVoiceActiveRef]);

  useEffect(() => {
    const runtime = liveCallRuntimeRef.current;
    if (!runtime) return;
    const sameSession = runtime.session_id === selectedSessionId;
    const sameCharacter = selectedSessionSummary?.interaction_mode === 'character'
      && runtime.interaction_mode === 'character'
      && runtime.character_id === selectedSessionSummary.character_id;
    if (!sameSession || !sameCharacter) {
      liveCallRuntimeRef.current = null;
      setLiveCallRuntime(null);
    }
  }, [selectedSessionId, selectedSessionSummary?.character_id, selectedSessionSummary?.interaction_mode]);

  // Avatar selection can update the live runtime without changing the chat
  // session or interaction query key. Keep the React-owned runtime in sync
  // with the bridge so the visible Live Voice controls and fullscreen surface
  // switch rigs immediately instead of retaining the previous model.
  useEffect(() => {
    const syncSelectedAvatar = (event: Event): void => {
      const runtime = (event as CustomEvent<CharacterLiveCallRuntime | null>).detail;
      if (!runtime || runtime.session_id !== selectedSessionId) return;
      liveCallRuntimeRef.current = runtime;
      setLiveCallRuntime(runtime);
    };
    window.addEventListener(CHARACTER_AVATAR_RUNTIME_EVENT, syncSelectedAvatar);
    return () => window.removeEventListener(CHARACTER_AVATAR_RUNTIME_EVENT, syncSelectedAvatar);
  }, [selectedSessionId]);

  return { liveCallRuntime, setLiveCallRuntime, liveCallRuntimeRef };
}
