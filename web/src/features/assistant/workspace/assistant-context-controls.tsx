import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import {
  MAX_DEEP_RESEARCH_PAGES,
  activeContextTools,
  desktopStatusLabel,
  researchModeAvailable,
  setAllowResearchDowngrade,
  setDeepResearchMaxPages,
  setResearchMode,
  toggleAgentMode,
  toggleDesktopShare,
  toggleLocalWorkspace,
  useAssistantContext,
  type ResearchMode,
} from './assistant-context-store';
import { shouldOfferResearchDowngrade } from './research-release-controller';
import { emitOmnixEvent } from '../../../events/bus';

const MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_CHAT_IMAGE_ATTACHMENTS = 8;
const MAX_CHAT_TEXT_FILE_BYTES = 100 * 1024;
const SUPPORTED_CHAT_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const SUPPORTED_CHAT_TEXT_FILE_SUFFIXES = new Set([
  '.c', '.cpp', '.cs', '.css', '.csv', '.go', '.h', '.hpp', '.htm', '.html', '.java', '.js', '.json', '.jsx', '.md', '.markdown', '.py', '.rs', '.sh', '.sql', '.text', '.ts', '.tsx', '.txt', '.xml', '.yaml', '.yml',
]);
const SUPPORTED_CHAT_TEXT_FILE_TYPES = new Set(['application/json', 'application/xml', 'text/csv', 'text/markdown', 'text/plain', 'text/xml']);
const FILE_ACCEPT = 'image/png,image/jpeg,image/webp,.txt,.text,.md,.markdown,.csv,.json,.yaml,.yml,.xml,.html,.htm,.css,.js,.jsx,.ts,.tsx,.py,.java,.c,.cpp,.h,.hpp,.go,.rs,.sh,.sql';

const RESEARCH_TOOLS: ReadonlyArray<{ mode: ResearchMode; title: string; description: string }> = [
  { mode: 'disabled', title: 'No web research', description: 'Use the assistant without live web results.' },
  { mode: 'quick', title: 'Quick search', description: 'Search the web for current information.' },
  { mode: 'deep', title: 'Deep research', description: 'Build a detailed, source-backed report.' },
];

function ToolItem({ role, checked, disabled, className, onClick, title, description, icon, data }: {
  role: 'menuitemradio' | 'menuitemcheckbox' | 'menuitem';
  checked?: boolean;
  disabled?: boolean;
  className?: string;
  onClick: () => void;
  title: string;
  description: ReactNode;
  icon?: string;
  data?: Record<string, string>;
}) {
  const classes = ['assistant-context-tool-item', className, checked ? 'active' : '', disabled ? 'unavailable' : ''].filter(Boolean).join(' ');
  return (
    <button type="button" role={role} className={classes} aria-checked={role === 'menuitem' ? undefined : checked} aria-disabled={disabled || undefined} disabled={disabled} onClick={onClick} {...data}>
      {icon ? <span className="assistant-context-tool-icon" aria-hidden="true">{icon}</span> : null}
      <span className="assistant-context-tool-copy"><strong>{title}</strong><small>{description}</small></span>
      {role === 'menuitem' ? null : <span className="assistant-context-tool-check" aria-hidden="true">✓</span>}
    </button>
  );
}

/**
 * The composer's "+" menu (attachments, web research, agent mode, desktop
 * sharing, local folder), the active-tool summary, the deep-research page
 * budget and the research release fallback.
 */
export function AssistantContextControls() {
  const context = useAssistantContext();
  const [open, setOpen] = useState(false);
  const toolsRef = useRef<HTMLDivElement | null>(null);
  const addButtonRef = useRef<HTMLButtonElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const summary = activeContextTools(context);

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"], [role="menuitemcheckbox"]')?.focus();
    const handlePointer = (event: PointerEvent) => {
      if (event.target instanceof Node && toolsRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener('pointerdown', handlePointer);
    return () => document.removeEventListener('pointerdown', handlePointer);
  }, [open]);

  function closeOnEscape(event: ReactKeyboardEvent, refocus: boolean): void {
    if (event.key !== 'Escape' || !open) return;
    setOpen(false);
    if (refocus) addButtonRef.current?.focus();
    event.preventDefault();
  }

  async function attach(files: File[]): Promise<void> {
    if (await dispatchChatAttachments(files)) setOpen(false);
  }

  return (
    <div className="assistant-context-controls" data-omnix-context-controls="true">
      <div className="assistant-context-tools" data-omnix-context-tools="true" ref={toolsRef}>
        <button
          ref={addButtonRef}
          type="button"
          className="assistant-context-add-button"
          aria-label="Add tools"
          aria-controls="assistant-context-tool-menu"
          aria-expanded={open}
          title="Add tools"
          onClick={() => setOpen((current) => !current)}
          onKeyDown={(event) => closeOnEscape(event, false)}
        >
          +
        </button>
        <span className="assistant-context-tool-summary" aria-live="polite" title={context.localWorkspace?.path || summary.join(' · ')} hidden={summary.length === 0}>
          {summary.join(' · ')}
        </span>
        <div id="assistant-context-tool-menu" ref={menuRef} className="assistant-context-tool-menu" role="menu" aria-label="Chat tools" hidden={!open} onKeyDown={(event) => closeOnEscape(event, true)}>
          <div className="assistant-context-tool-menu-heading">Add to this chat</div>
          <span className="assistant-context-tool-file-control" role="none">
            <ToolItem role="menuitem" className="assistant-context-tool-item-file" icon={'\u{1F4CE}'} title="Add photos & files" description="Images or text documents from computer" onClick={() => fileInputRef.current?.click()} data={{ 'data-omnix-context-tool-files': 'true' }} />
            <input
              ref={fileInputRef}
              type="file"
              className="visually-hidden"
              accept={FILE_ACCEPT}
              multiple
              aria-label="Choose photos and files from computer"
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => {
                const files = Array.from(event.currentTarget.files ?? []);
                event.currentTarget.value = '';
                if (files.length) void attach(files);
              }}
            />
          </span>
          {RESEARCH_TOOLS.map((tool) => {
            const unavailable = !researchModeAvailable(tool.mode, context.availability);
            return (
              <ToolItem key={tool.mode} role="menuitemradio" checked={context.researchMode === tool.mode} disabled={unavailable} title={tool.title} description={tool.description} onClick={() => setResearchMode(tool.mode)} data={{ 'data-omnix-context-tool-mode': tool.mode }} />
            );
          })}
          <div className="assistant-context-tool-menu-divider" role="separator" />
          <ToolItem role="menuitemcheckbox" checked={context.agentMode} title="Agent mode" description="Route every request through the Pi coding agent." onClick={toggleAgentMode} data={{ 'data-omnix-context-tool-agent': 'true' }} />
          <ToolItem role="menuitemcheckbox" checked={context.desktopShare !== null} title="Desktop" description="Share a live desktop view with the assistant." onClick={() => void toggleDesktopShare()} data={{ 'data-omnix-context-tool-desktop': 'true' }} />
          <ToolItem
            role="menuitemcheckbox"
            checked={context.localWorkspace !== null}
            title="Local folder"
            description={<span data-omnix-local-folder-detail="true">{context.localWorkspace?.path || context.localWorkspaceStatus || 'Attach a local project or workspace folder.'}</span>}
            onClick={() => void toggleLocalWorkspace()}
            data={{ 'data-omnix-context-tool-local-folder': 'true', title: context.localWorkspace?.path || context.localWorkspaceStatus || 'Attach a local project or workspace folder' }}
          />
          <label className="assistant-context-page-budget assistant-context-page-budget-menu" data-omnix-deep-research-pages="true" hidden={context.researchMode !== 'deep'}>
            <span>Max pages</span>
            <input
              type="number"
              min={1}
              max={MAX_DEEP_RESEARCH_PAGES}
              step={1}
              inputMode="numeric"
              aria-label="Maximum pages to search"
              key={context.deepResearchMaxPages}
              defaultValue={context.deepResearchMaxPages}
              onChange={(event) => setDeepResearchMaxPages(event.currentTarget.value)}
            />
          </label>
        </div>
      </div>
      <div className="assistant-research-release-controls" data-omnix-research-release="true">
        <label className="assistant-research-fallback" hidden={!shouldOfferResearchDowngrade(context.researchMode, context.availability)}>
          <input type="checkbox" aria-label="Allow Quick Search fallback" checked={context.allowDowngrade} onChange={(event) => setAllowResearchDowngrade(event.currentTarget.checked)} />
          <span>Allow Quick fallback</span>
        </label>
        <small className="assistant-research-release-status" role="status">{context.releaseMessage}</small>
      </div>
    </div>
  );
}

/** The composer's desktop sharing toggle. */
export function DesktopShareButton() {
  const sharing = useAssistantContext().desktopShare !== null;
  return (
    <button type="button" className={`assistant-context-desktop-inline assistant-context-desktop${sharing ? ' active' : ''}`} data-omnix-desktop-action="true" aria-label={sharing ? 'Stop desktop sharing' : 'Start desktop sharing'} onClick={() => void toggleDesktopShare()}>
      <span>Desktop</span><strong>{sharing ? 'Sharing' : 'Off'}</strong>
    </button>
  );
}

/** The audio services row for desktop sharing. */
export function DesktopShareStatusRow() {
  const context = useAssistantContext();
  return (
    <div data-omnix-desktop-status="true">
      <span>Desktop</span>
      <strong className="assistant-desktop-status-value">{desktopStatusLabel(context.desktopShare !== null, context.desktopStatus)}</strong>
      <i aria-hidden="true" />
    </div>
  );
}

/** Sends chosen files to the composer (images, or one text document); true when they were accepted. */
async function dispatchChatAttachments(files: File[]): Promise<boolean> {
  const imageFiles = files.filter((file) => SUPPORTED_CHAT_IMAGE_TYPES.has(file.type));
  if (imageFiles.length === files.length) {
    if (imageFiles.length > MAX_CHAT_IMAGE_ATTACHMENTS) return attachmentError(`Attach at most ${MAX_CHAT_IMAGE_ATTACHMENTS} images at a time.`);
    if (imageFiles.some((file) => file.size > MAX_CHAT_IMAGE_BYTES)) {
      return attachmentError('Each image must be 5 MB or smaller.');
    }
    try {
      const images = await Promise.all(imageFiles.map(async (file) => ({ dataUrl: await readFileAsDataUrl(file), mimeType: file.type, size: file.size })));
      for (const image of images) emitOmnixEvent('omnix:chat-image-selected', image);
      return true;
    } catch {
      return attachmentError('Unable to read one or more selected images.');
    }
  }
  if (files.length !== 1) return attachmentError('Select multiple images together, or attach one text document separately.');

  const [file] = files;
  const mimeType = chatTextFileMimeType(file);
  if (!mimeType) return attachmentError('Choose a PNG, JPEG, WebP, or supported text document.');
  if (file.size > MAX_CHAT_TEXT_FILE_BYTES) return attachmentError('That text file is larger than 100 KB. Choose a smaller file.');
  try {
    const text = await file.text();
    if (!text.trim() || text.length > MAX_CHAT_TEXT_FILE_BYTES) return attachmentError('The selected text file is empty or larger than 100 KB.');
    emitOmnixEvent('omnix:chat-text-file-selected', { filename: file.name, mimeType, size: file.size, text });
    return true;
  } catch {
    return attachmentError('Unable to read the selected text file.');
  }
}

function chatTextFileMimeType(file: File): string | null {
  const name = file.name.toLowerCase();
  const suffix = name.slice(name.lastIndexOf('.'));
  if (!SUPPORTED_CHAT_TEXT_FILE_TYPES.has(file.type) && !SUPPORTED_CHAT_TEXT_FILE_SUFFIXES.has(suffix)) return null;
  return SUPPORTED_CHAT_TEXT_FILE_TYPES.has(file.type) ? file.type : 'text/plain';
}

function attachmentError(message: string): false {
  emitOmnixEvent('omnix:chat-image-error', { message });
  return false;
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('Image data was not text.'));
    reader.onerror = () => reject(reader.error ?? new Error('Image read failed.'));
    reader.readAsDataURL(file);
  });
}
