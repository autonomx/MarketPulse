import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { omnixApiClient } from '../../../api/client';
import { characterClient } from './characterClient';
import { chatCapableModels, chatCapableProviders, getVoiceProfileAssets, type AssistantView } from './chatbotWorkspaceModel';
import type { ChatSessionListEntry } from './useChatSessions';
import { voiceApiClient } from '../../voice';

type ChatCatalogOptions = {
  activeView: AssistantView;
  /** Voice profiles wait until the session list has loaded. */
  sessionsPending: boolean;
  selectedSessionId: string | null;
  selectedSessionSummary: ChatSessionListEntry | undefined;
  selectedProviderId: string;
};

/** What Chat chooses from: providers and models, voice profiles, and the selected session's character. */
export function useChatCatalog({ activeView, sessionsPending, selectedSessionId, selectedSessionSummary, selectedProviderId }: ChatCatalogOptions) {
  const providerQuery = useQuery({ queryKey: ['platform', 'providers'], queryFn: () => omnixApiClient.listProviders() });
  const assetsQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'voice-library'],
    queryFn: async () => {
      try {
        return await voiceApiClient.listVoiceLibrary();
      } catch {
        // Older gateways do not expose the narrow route yet. Preserve the
        // compatibility path without making the aggregate catalog part of
        // the chat-history request when the direct route is available.
        return omnixApiClient.listAssets();
      }
    },
    // Voice profiles are a settings/voice concern. Defer even the narrow
    // voice-library request until chat history has settled.
    enabled: !sessionsPending && (activeView === 'chats' || activeView === 'settings' || activeView === 'voice'),
  });
  const interactionQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'interaction', selectedSessionId],
    queryFn: () => characterClient.session(selectedSessionId ?? ''),
    // Character identity is needed by the Chat view's right rail as well as
    // the dedicated Voice view. System chats stay off this endpoint.
    enabled: Boolean(
      selectedSessionId
      && (activeView === 'voice' || selectedSessionSummary?.interaction_mode === 'character'),
    ),
  });
  const providerPayload = providerQuery.data;
  const chatProviders = useMemo(() => chatCapableProviders(providerPayload), [providerPayload]);
  const chatModels = useMemo(() => chatCapableModels(providerPayload, selectedProviderId), [providerPayload, selectedProviderId]);
  const selectedCharacterId = selectedSessionSummary?.interaction_mode === 'character'
    ? selectedSessionSummary.character_id
    : null;
  const charactersQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'characters'],
    queryFn: () => characterClient.list(),
    enabled: Boolean(selectedCharacterId && (activeView === 'chats' || activeView === 'voice')),
  });
  const selectedCharacter = charactersQuery.data?.characters.find((character) => character.id === selectedCharacterId);
  const voiceProfiles = useMemo(() => getVoiceProfileAssets(assetsQuery.data), [assetsQuery.data]);
  return {
    providerPayload,
    chatProviders,
    chatModels,
    interaction: interactionQuery.data,
    selectedCharacter,
    voiceProfiles,
    voiceProfilesLoading: assetsQuery.isLoading,
  };
}
