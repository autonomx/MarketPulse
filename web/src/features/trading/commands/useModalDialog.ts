import { useEffect, useRef, type KeyboardEvent, type RefObject } from 'react';

const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled]):not([type="hidden"])', 'select:not([disabled])',
  'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(', ');

/**
 * Keyboard behaviour shared by the palette, the interval box and the shortcut
 * dialog: Tab and Shift+Tab stay inside the dialog, Escape closes it from
 * anywhere inside, and closing returns focus to whatever had it before the
 * dialog opened. Returns the dialog's keydown handler.
 */
export function useModalDialog(open: boolean, dialogRef: RefObject<HTMLElement | null>, onClose: () => void) {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return undefined;
    const opener = document.activeElement;
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, [open]);

  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.defaultPrevented) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onCloseRef.current();
      return;
    }
    const dialog = dialogRef.current;
    if (event.key !== 'Tab' || !dialog) return;
    const focusable = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)];
    const first = focusable[0];
    const last = focusable.at(-1);
    const active = document.activeElement;
    const leaving = event.shiftKey ? active === first || !dialog.contains(active) : active === last || !dialog.contains(active);
    if (!first || !last || leaving) {
      event.preventDefault();
      (event.shiftKey ? last : first)?.focus();
    }
  };
}
