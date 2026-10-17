import type { ClipboardEvent as ReactClipboardEvent, FormEventHandler, KeyboardEvent } from 'react';
import type { FieldErrors, UseFormRegister } from 'react-hook-form';
import { AssistantContextControls, DesktopShareButton } from '../workspace';
import { suggestedPrompts, type ChatbotFormValues, type PastedChatImage, type PastedChatTextFile } from './chatbotWorkspaceModel';

type ChatComposerOption = { id: string; label: string };

type ChatComposerProps = {
  onSubmit: FormEventHandler<HTMLFormElement>;
  register: UseFormRegister<ChatbotFormValues>;
  errors: FieldErrors<ChatbotFormValues>;
  chatProviders: ChatComposerOption[];
  chatModels: ChatComposerOption[];
  onApplyPrompt: (prompt: string) => void;
  ttsOutputLabel: string;
  canPlayLatestReply: boolean;
  onPlayLatestReply: () => void;
  personalityLabel: string;
  permissionsLabel: string | undefined;
  toolsLabel: string;
  contextLabel: string;
  onOpenSettings: () => void;
  onOpenTools: () => void;
  onRefreshContext: () => void;
  pastedChatImages: PastedChatImage[];
  pastedChatTextFile: PastedChatTextFile | null;
  chatImageError: string | null;
  onRemoveImage: (index: number) => void;
  onRemoveTextFile: () => void;
  onPaste: (event: ReactClipboardEvent<HTMLTextAreaElement>) => void;
  liveVoiceActive: boolean;
  onToggleCall: () => void;
  sendPending: boolean;
  chatJobInProgress: boolean;
};

/** The message composer: prompts, conversation controls, attachments, the message and its actions. */
export function ChatComposer({
  onSubmit,
  register,
  errors,
  chatProviders,
  chatModels,
  onApplyPrompt,
  ttsOutputLabel,
  canPlayLatestReply,
  onPlayLatestReply,
  personalityLabel,
  permissionsLabel,
  toolsLabel,
  contextLabel,
  onOpenSettings,
  onOpenTools,
  onRefreshContext,
  pastedChatImages,
  pastedChatTextFile,
  chatImageError,
  onRemoveImage,
  onRemoveTextFile,
  onPaste,
  liveVoiceActive,
  onToggleCall,
  sendPending,
  chatJobInProgress,
}: ChatComposerProps) {
  return (
    <form className="assistant-composer" onSubmit={onSubmit}>
      <div role="group" className="assistant-suggestion-row" aria-label="Suggested prompts">
        {suggestedPrompts.map((prompt) => <button key={prompt} type="button" onClick={() => onApplyPrompt(prompt)}>{prompt}</button>)}
        <button type="button" onClick={() => onApplyPrompt('Give me more options for this conversation')}>More</button>
      </div>
      <div role="group" className="assistant-composer-controls" aria-label="Conversation controls">
        <label><span>Provider</span><select {...register('providerId')} aria-label="Provider"><option value="">Default provider</option>{chatProviders.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}</select></label>
        <label><span>Model</span><select {...register('modelId')} aria-label="Model"><option value="">Default model</option>{chatModels.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}</select></label>
        <button type="button" className="assistant-composer-chip" onClick={onPlayLatestReply} disabled={!canPlayLatestReply}><span>Voice</span><strong>{ttsOutputLabel}</strong></button>
        <button className="assistant-composer-chip" type="button" onClick={onOpenSettings}><span>Personality</span><strong>{personalityLabel}</strong></button>
        <button className="assistant-composer-chip" type="button" onClick={onOpenSettings}><span>Permissions</span><strong>{permissionsLabel}</strong></button>
        <button type="button" className="assistant-composer-chip" onClick={onOpenTools}><span>Tools</span><strong>{toolsLabel}</strong></button>
        <button type="button" className="assistant-composer-chip" onClick={onRefreshContext}><span>Context</span><strong>{contextLabel}</strong></button>
      </div>
      {pastedChatImages.length ? <div className="assistant-chat-image-attachments" role="status" aria-label={`${pastedChatImages.length} image attachment${pastedChatImages.length === 1 ? '' : 's'}`}>{pastedChatImages.map((image, index) => <div className="assistant-chat-image-attachment" key={`${image.dataUrl.slice(-24)}:${index}`}><img src={image.dataUrl} alt={`Attached image preview ${index + 1}`} /><div><strong>Image {index + 1}</strong><small>{image.mimeType.replace('image/', '').toUpperCase()} · {(image.size / 1024).toFixed(0)} KB</small></div><button type="button" aria-label={`Remove attached image ${index + 1}`} onClick={() => onRemoveImage(index)}>×</button></div>)}</div> : null}
      {pastedChatTextFile ? <div className="assistant-chat-file-attachment" role="status"><span aria-hidden="true">📄</span><div><strong>{pastedChatTextFile.filename}</strong><small>{pastedChatTextFile.mimeType} · {(pastedChatTextFile.size / 1024).toFixed(0)} KB</small></div><button type="button" aria-label="Remove attached file" onClick={onRemoveTextFile}>×</button></div> : null}
      {chatImageError ? <p className="assistant-chat-image-error" role="alert">{chatImageError}</p> : null}
      <label className="assistant-message-input"><span>Message <small className="assistant-chat-paste-hint">Paste an image, or use + to add a photo or text file</small></span><textarea rows={3} aria-label="Message" aria-invalid={Boolean(errors.content)} placeholder="Message Omnix Assistant, or use the microphone…" onKeyDown={handleComposerTextareaKeyDown} onPaste={onPaste} {...register('content', { validate: (value) => (value.trim() || pastedChatImages.length > 0 || pastedChatTextFile) ? true : 'Enter a message, paste an image, or add a file before sending.' })} /></label>
      <div className="assistant-composer-actions"><DesktopShareButton /><button type="button" className="assistant-mic-button" aria-label={liveVoiceActive ? 'Stop voice input' : 'Start voice input'} onClick={onToggleCall}>{liveVoiceActive ? '■' : '◉'}</button><button aria-label={sendPending ? 'Queueing response' : chatJobInProgress ? 'Interrupt and send' : 'Queue response'} className="assistant-send-button" type="submit" disabled={sendPending}>{sendPending ? 'Queueing response…' : chatJobInProgress ? 'Interrupt & send' : 'Send message'}</button></div>
    <AssistantContextControls />
    </form>
  );
}

// Enter sends; Shift+Enter (or an IME composition) keeps typing.
function handleComposerTextareaKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
  if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey || event.nativeEvent.isComposing) return;

  event.preventDefault();
  event.currentTarget.form?.requestSubmit();
}

