export type PlaybackItem = {
  id: string;
  text: string;
  createdAt: string;
};

export type PlaybackQueue = {
  items: PlaybackItem[];
  activeItemId?: string;
};

export function createPlaybackQueue(items: PlaybackItem[] = []): PlaybackQueue {
  return { items: items.map((item) => ({ ...item })) };
}

export function enqueuePlaybackItem(queue: PlaybackQueue, item: PlaybackItem): PlaybackQueue {
  return { ...queue, items: [...queue.items, { ...item }] };
}

export function setActivePlaybackItem(queue: PlaybackQueue, itemId: string): PlaybackQueue {
  return { ...queue, activeItemId: itemId };
}
