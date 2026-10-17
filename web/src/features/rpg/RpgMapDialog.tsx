import { useEffect, useRef } from 'react';
import { RpgMapSurface } from './RpgMapSurface';
import './RpgMapDialog.css';

interface RpgMapDialogProps {
  locationLabel: string;
  mapId: string;
  onClose: () => void;
  open: boolean;
  sessionId: string;
}

export function RpgMapDialog({ locationLabel, mapId, onClose, open, sessionId }: RpgMapDialogProps) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      previousFocus?.focus();
    };
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div className="rpg-map-dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section
        aria-labelledby="rpg-map-dialog-title"
        aria-modal="true"
        className="rpg-map-dialog"
        role="dialog"
      >
        <header className="rpg-map-dialog-header">
          <div>
            <p className="eyebrow">Interactive world map</p>
            <h2 id="rpg-map-dialog-title">{locationLabel}</h2>
            <small>{mapId}</small>
          </div>
          <button aria-label="Close map" onClick={onClose} ref={closeButtonRef} type="button">
            ×
          </button>
        </header>
        <RpgMapSurface mapId={mapId} sessionId={sessionId} />
      </section>
    </div>
  );
}
