/**
 * Saves a file in the browser. A temporary link is the only browser API for
 * this; it is added to the page just long enough to be clicked.
 */
export function downloadUrl(url: string, filename: string): void {
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
}

/** Saves generated content as a file. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  downloadUrl(url, filename);
  // Revoked after the click has been handled.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Saves a value as a formatted JSON file. */
export function downloadJson(value: unknown, filename: string): void {
  downloadBlob(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }), filename);
}
