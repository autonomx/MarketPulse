export type LibraryItemStatus = 'pending' | 'ready' | 'failed';

export type LibraryItem = {
  id: string;
  workspaceId: string;
  projectId?: string;
  title: string;
  status: LibraryItemStatus;
};

export type LibrarySegment = {
  id: string;
  itemId: string;
  text: string;
};

export function getReadyLibraryItems(items: LibraryItem[]): LibraryItem[] {
  return items.filter((item) => item.status === 'ready');
}

export function getScopedLibraryItems(items: LibraryItem[], workspaceId: string, projectId?: string): LibraryItem[] {
  return items.filter((item) => item.workspaceId === workspaceId && (!projectId || item.projectId === undefined || item.projectId === projectId));
}

export function getSegmentsForItems(segments: LibrarySegment[], items: LibraryItem[]): LibrarySegment[] {
  const itemIds = new Set(items.map((item) => item.id));
  return segments.filter((segment) => itemIds.has(segment.itemId));
}
