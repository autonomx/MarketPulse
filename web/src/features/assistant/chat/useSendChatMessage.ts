import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type Dispatch, type SetStateAction } from 'react';
import type { UseFormReset } from 'react-hook-form';
import { ApiError, omnixApiClient, type SendChatMessageRequest } from '../../../api/client';
import { assistantContextStore, createChatbotFailureEvent, type AssistantWorkspaceEvent, type AssistantWorkspaceEventStoreFilter, type AssistantWorkspaceRuntimeConfig, sendChatWithAssistantContext } from '../workspace';
import { noteChatMessageSent } from './researchProgressController';
import {
  attachmentDefaultMessage,
  chatbotSubmitErrorMessage,
  createPersonalityPrompt,
  createWorkspaceEventFilter,
  type AssistantSettings,
  type ChatMessage,
  type ChatbotFormValues,
  type PastedChatImage,
  type PastedChatTextFile,
} from './chatbotWorkspaceModel';
import { assistantApiClient, type AssistantContextChatRequest } from '../api/assistantClient';

type SendChatMessageOptions = {
  runtimeConfig: AssistantWorkspaceRuntimeConfig;
  assistantSettings: AssistantSettings;
  selectedSessionId: string | null;
  setSelectedSessionId: (sessionId: string | null) => void;
  pastedChatImages: PastedChatImage[];
  pastedChatTextFile: PastedChatTextFile | null;
  setPastedChatImages: Dispatch<SetStateAction<PastedChatImage[]>>;
  setPastedChatTextFile: Dispatch<SetStateAction<PastedChatTextFile | null>>;
  setChatImageError: Dispatch<SetStateAction<string | null>>;
  setActiveChatJobId: (jobId: string | null) => void;
  setChatJobError: (error: string | null) => void;
  reset: UseFormReset<ChatbotFormValues>;
  /** Called once a message is queued: clears the voice draft. */
  onSent: () => void;
  /** Shows a failed send in the activity panel. */
  recordActivityEvent: (event: AssistantWorkspaceEvent, filter: AssistantWorkspaceEventStoreFilter) => void;
  markVoiceTurnPerformance: (stage: 'chatSubmitStartedAt' | 'chatResponseReceivedAt') => void;
};

/**
 * Sends the composer's message as a queued reply job. The message shows at
 * once; a retry of the same content reuses the same idempotency key.
 */
export function useSendChatMessage({
  runtimeConfig,
  assistantSettings,
  selectedSessionId,
  setSelectedSessionId,
  pastedChatImages,
  pastedChatTextFile,
  setPastedChatImages,
  setPastedChatTextFile,
  setChatImageError,
  setActiveChatJobId,
  setChatJobError,
  reset,
  onSent,
  recordActivityEvent,
  markVoiceTurnPerformance,
}: SendChatMessageOptions) {
  const queryClient = useQueryClient();
  const [quickSearchProgress, setQuickSearchProgress] = useState<string | null>(null);
  const [pendingUserMessage, setPendingUserMessage] = useState<ChatMessage | null>(null);
  const pendingChatSubmissionRef = useRef<{ fingerprint: string; id: string } | null>(null);

  const sendMutation = useMutation({
    mutationFn: async (values: ChatbotFormValues) => {
      const providerId = values.providerId || undefined;
      const modelId = values.modelId || undefined;
      const content = values.content.trim() || attachmentDefaultMessage(pastedChatImages, pastedChatTextFile);
      const personalityPrompt = createPersonalityPrompt(assistantSettings);
      let sessionId = selectedSessionId;
      if (!sessionId) {
        const created = await omnixApiClient.createChatSession({
          title: content.slice(0, 48) || 'New chat',
          provider_id: providerId,
          model_id: modelId,
          system_prompt: personalityPrompt || undefined,
        });
        sessionId = created.id;
        setSelectedSessionId(sessionId);
      }
      const message: SendChatMessageRequest = {
        content,
        user_turn_id: values.userTurnId,
        provider_id: providerId,
        model_id: modelId,
        coding_approval_policy: assistantSettings.codingApprovalPolicy,
        image_data_urls: pastedChatImages.map((image) => image.dataUrl),
        text_attachment: pastedChatTextFile
          ? { filename: pastedChatTextFile.filename, mime_type: pastedChatTextFile.mimeType, text: pastedChatTextFile.text }
          : undefined,
      };
      const chatSessionId = sessionId;
      return noteChatMessageSent(sessionId, await sendChatWithAssistantContext(chatSessionId, message, (route, body) => (
        route === 'context'
          ? assistantApiClient.sendAssistantContextChatMessage(chatSessionId, body as AssistantContextChatRequest)
          : omnixApiClient.sendChatMessage(chatSessionId, body as SendChatMessageRequest)
      )));
    },
    onMutate: (values) => {
      markVoiceTurnPerformance('chatSubmitStartedAt');
      setChatJobError(null);
      const researchMode = assistantContextStore.getState().researchMode;
      const content = values.content.trim() || attachmentDefaultMessage(pastedChatImages, pastedChatTextFile);
      setQuickSearchProgress(researchMode === 'quick' ? content : null);
      setPendingUserMessage({
        id: `optimistic-user-${Date.now()}`,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
        metadata: {
          ...(pastedChatImages.length > 0 ? {
            image_data_urls: pastedChatImages.map((image) => image.dataUrl),
            image_data_url: pastedChatImages[0].dataUrl,
          } : {}),
          ...(pastedChatTextFile ? { text_attachment: { filename: pastedChatTextFile.filename, mime_type: pastedChatTextFile.mimeType, text: pastedChatTextFile.text } } : {}),
        },
      });
    },
    onSuccess: (_result, values) => {
      markVoiceTurnPerformance('chatResponseReceivedAt');
      if (pendingChatSubmissionRef.current?.id === values.userTurnId) {
        pendingChatSubmissionRef.current = null;
      }
      setActiveChatJobId(_result.job.id);
      setChatJobError(null);
      setQuickSearchProgress(null);
      setPendingUserMessage(null);
      reset({ content: '', providerId: values.providerId, modelId: values.modelId });
      setPastedChatImages([]);
      setPastedChatTextFile(null);
      setChatImageError(null);
      onSent();
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot'] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] });
    },
    onError: (error, values) => {
      setQuickSearchProgress(null);
      setPendingUserMessage(null);
      // Keep the submission identity after an ambiguous transport/server error.
      // Retrying the same payload must reuse the same idempotency key.
      setActiveChatJobId(null);
      const sessionId = selectedSessionId ?? undefined;
      const filter = createWorkspaceEventFilter(runtimeConfig, sessionId);
      const failureEvent = createChatbotFailureEvent({
        workspaceId: runtimeConfig.workspaceId,
        projectId: runtimeConfig.projectId,
        sessionId,
        providerId: values.providerId || runtimeConfig.defaultProviderId,
        modelId: values.modelId || runtimeConfig.defaultModelId,
        message: chatbotSubmitErrorMessage(error),
        ...(error instanceof ApiError ? { statusCode: error.status } : {}),
        submittedContent: values.content,
        createdAt: new Date().toISOString(),
      });
      recordActivityEvent(failureEvent, filter);
    },
  });

  function chatSubmissionId(values: ChatbotFormValues): string {
    const fingerprint = JSON.stringify([
      values.content.trim(),
      values.providerId,
      values.modelId,
      pastedChatImages.map((image) => image.dataUrl),
      pastedChatTextFile?.filename ?? null,
      pastedChatTextFile?.text ?? null,
    ]);
    if (pendingChatSubmissionRef.current?.fingerprint === fingerprint) {
      return pendingChatSubmissionRef.current.id;
    }
    const id = `web-user-turn:${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
    pendingChatSubmissionRef.current = { fingerprint, id };
    return id;
  }

  function sendChatMessage(values: ChatbotFormValues): void {
    sendMutation.mutate({ ...values, userTurnId: chatSubmissionId(values) });
  }

  return { sendMutation, sendChatMessage, quickSearchProgress, pendingUserMessage };
}
