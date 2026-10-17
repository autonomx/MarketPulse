import { useEffect, useState, type ClipboardEvent as ReactClipboardEvent } from 'react';
import { MAX_CHAT_IMAGE_ATTACHMENTS, SUPPORTED_CHAT_IMAGE_TYPES, chatImageDataUrls } from './chatMessageModel';
import {
  MAX_CHAT_IMAGE_BYTES,
  MAX_CHAT_TEXT_FILE_BYTES,
  readFileAsDataUrl,
  type PastedChatImage,
  type PastedChatTextFile,
} from './chatbotWorkspaceModel';

/** Images and a text file attached to the next message: pasted, or chosen from the composer's tools menu. */
export function useChatAttachments() {
  const [pastedChatImages, setPastedChatImages] = useState<PastedChatImage[]>([]);
  const [pastedChatTextFile, setPastedChatTextFile] = useState<PastedChatTextFile | null>(null);
  const [chatImageError, setChatImageError] = useState<string | null>(null);

  useEffect(() => {
    const handleSelectedChatImage = (event: Event): void => {
      const detail = (event as CustomEvent<Partial<PastedChatImage>>).detail;
      if (!detail || typeof detail.dataUrl !== 'string' || typeof detail.mimeType !== 'string' || !SUPPORTED_CHAT_IMAGE_TYPES.has(detail.mimeType) || chatImageDataUrls({ image_data_url: detail.dataUrl }).length !== 1) {
        setChatImageError('The selected file is not a supported image.');
        return;
      }
      const image = {
        dataUrl: detail.dataUrl,
        mimeType: detail.mimeType,
        size: typeof detail.size === 'number' && Number.isFinite(detail.size) ? detail.size : 0,
      };
      if (pastedChatImages.length >= MAX_CHAT_IMAGE_ATTACHMENTS && !pastedChatImages.some((candidate) => candidate.dataUrl === image.dataUrl)) {
        setChatImageError(`You can attach up to ${MAX_CHAT_IMAGE_ATTACHMENTS} images.`);
        return;
      }
      setPastedChatImages((current) => {
        if (current.some((candidate) => candidate.dataUrl === image.dataUrl)) return current;
        return [...current, image].slice(0, MAX_CHAT_IMAGE_ATTACHMENTS);
      });
      setPastedChatTextFile(null);
      setChatImageError(null);
    };
    const handleSelectedChatTextFile = (event: Event): void => {
      const detail = (event as CustomEvent<Partial<PastedChatTextFile>>).detail;
      if (!detail || typeof detail.filename !== 'string' || typeof detail.mimeType !== 'string' || typeof detail.text !== 'string' || !detail.filename.trim() || !detail.mimeType.trim() || !detail.text.trim() || detail.text.length > MAX_CHAT_TEXT_FILE_BYTES) {
        setChatImageError('The selected file is empty or too large. Choose a text file smaller than 100 KB.');
        return;
      }
      setPastedChatTextFile({
        filename: detail.filename.trim(),
        mimeType: detail.mimeType.trim(),
        size: typeof detail.size === 'number' && Number.isFinite(detail.size) ? detail.size : detail.text.length,
        text: detail.text,
      });
      setPastedChatImages([]);
      setChatImageError(null);
    };
    const handleChatImageError = (event: Event): void => {
      const detail = (event as CustomEvent<{ message?: unknown }>).detail;
      setChatImageError(typeof detail?.message === 'string' ? detail.message : 'Unable to attach the selected image.');
    };
    window.addEventListener('omnix:chat-image-selected', handleSelectedChatImage);
    window.addEventListener('omnix:chat-text-file-selected', handleSelectedChatTextFile);
    window.addEventListener('omnix:chat-image-error', handleChatImageError);
    return () => {
      window.removeEventListener('omnix:chat-image-selected', handleSelectedChatImage);
      window.removeEventListener('omnix:chat-text-file-selected', handleSelectedChatTextFile);
      window.removeEventListener('omnix:chat-image-error', handleChatImageError);
    };
  }, [pastedChatImages]);

  function handleComposerPaste(event: ReactClipboardEvent<HTMLTextAreaElement>): void {
    const imageItems = Array.from(event.clipboardData.items).filter((item) => item.type.startsWith('image/'));
    if (!imageItems.length) return;

    event.preventDefault();
    const files = imageItems.map((item) => item.getAsFile()).filter((file): file is File => Boolean(file));
    if (files.length !== imageItems.length) {
      setChatImageError('Unable to read one or more pasted images.');
      return;
    }
    if (files.some((file) => !SUPPORTED_CHAT_IMAGE_TYPES.has(file.type))) {
      setChatImageError('Paste PNG, JPEG, or WebP images.');
      return;
    }
    if (files.some((file) => file.size > MAX_CHAT_IMAGE_BYTES)) {
      setChatImageError('Each image must be 5 MB or smaller.');
      return;
    }
    if (files.length > MAX_CHAT_IMAGE_ATTACHMENTS - pastedChatImages.length) {
      setChatImageError(`You can attach up to ${MAX_CHAT_IMAGE_ATTACHMENTS} images.`);
      return;
    }

    setChatImageError(null);
    void Promise.all(files.map(async (file) => ({
      dataUrl: await readFileAsDataUrl(file),
      mimeType: file.type,
      size: file.size,
    })))
      .then((images) => {
        setPastedChatImages((current) => {
          const next = [...current];
          for (const image of images) {
            if (next.length >= MAX_CHAT_IMAGE_ATTACHMENTS) break;
            if (!next.some((candidate) => candidate.dataUrl === image.dataUrl)) next.push(image);
          }
          return next;
        });
        setPastedChatTextFile(null);
      })
      .catch(() => setChatImageError('Unable to read one or more pasted images.'));
  }

  return {
    pastedChatImages, setPastedChatImages,
    pastedChatTextFile, setPastedChatTextFile,
    chatImageError, setChatImageError,
    handleComposerPaste,
  };
}
