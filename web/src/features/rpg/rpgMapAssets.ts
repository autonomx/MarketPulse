export function rpgMapAssetUrl(assetId: string | null | undefined): string | null {
  const normalized = assetId?.trim();
  if (!normalized || normalized.includes('/') || normalized.includes('\\') || normalized.startsWith('data:')) {
    return null;
  }
  return `/api/assets/${encodeURIComponent(normalized)}/file`;
}

export function rpgMapAssetLabel(assetId: string | null | undefined): string {
  const normalized = assetId?.trim() ?? '';
  return normalized.includes(':') ? normalized.split(':').at(-1)?.replaceAll('-', ' ') ?? normalized : normalized;
}
