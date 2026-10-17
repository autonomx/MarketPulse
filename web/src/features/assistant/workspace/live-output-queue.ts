export type LiveOutputPriority = 'control' | 'critical' | 'normal' | 'deferred';
export type LiveOutputStatus =
  | 'candidate'
  | 'queued'
  | 'generating'
  | 'buffered'
  | 'playing'
  | 'completed'
  | 'cancelled'
  | 'failed';

export type LiveOutputDeliveryState = {
  generatedTextEnd: number;
  audioBufferedTextEnd: number;
  audioDeliveredTextEnd: number;
  visualDeliveredTextEnd: number;
  contextDeliveredTextEnd: number;
};

export type LiveOutputItem = {
  outputId: string;
  generationEpoch: number;
  outputOrder: number;
  observationId?: string;
  taskContractId: string;
  taskContractVersion: number;
  contextVersion: number;
  anchorIds: string[];
  priority: LiveOutputPriority;
  status: LiveOutputStatus;
  estimatedSpeechMs: number;
  cancellationReason?: string;
  delivery: LiveOutputDeliveryState;
};

export type LiveOutputQueueOptions = {
  maxItems?: number;
  maxSpeechMs?: number;
};

const PRIORITY_ORDER: Record<LiveOutputPriority, number> = {
  control: 0,
  critical: 1,
  normal: 2,
  deferred: 3,
};

export class LiveOutputQueue {
  private readonly items = new Map<string, LiveOutputItem>();
  private readonly orderedIds: string[] = [];
  private readonly epochs = new Map<string, number>();
  private nextOrder = 0;
  private readonly maxItems: number;
  private readonly maxSpeechMs: number;

  constructor(options: LiveOutputQueueOptions = {}) {
    this.maxItems = Math.max(1, options.maxItems ?? 24);
    this.maxSpeechMs = Math.max(250, options.maxSpeechMs ?? 30_000);
  }

  get snapshot(): readonly LiveOutputItem[] {
    return this.orderedIds.map((id) => this.items.get(id)).filter((item): item is LiveOutputItem => Boolean(item));
  }

  get activeSpeechMs(): number {
    return this.snapshot
      .filter((item) => !isTerminal(item.status))
      .reduce((total, item) => total + item.estimatedSpeechMs, 0);
  }

  nextEpoch(outputId: string): number {
    const epoch = (this.epochs.get(outputId) ?? 0) + 1;
    this.epochs.set(outputId, epoch);
    return epoch;
  }

  enqueue(input: Omit<LiveOutputItem, 'generationEpoch' | 'outputOrder' | 'status' | 'delivery'> & {
    generationEpoch?: number;
    delivery?: Partial<LiveOutputDeliveryState>;
  }): LiveOutputItem {
    const generationEpoch = input.generationEpoch ?? this.nextEpoch(input.outputId);
    const existing = this.items.get(input.outputId);
    if (existing && !isTerminal(existing.status)) throw new Error('output_item_active');
    if (this.snapshot.filter((item) => !isTerminal(item.status)).length >= this.maxItems) {
      throw new Error('output_item_limit');
    }
    if (this.activeSpeechMs + input.estimatedSpeechMs > this.maxSpeechMs && input.priority !== 'control' && input.priority !== 'critical') {
      throw new Error('output_speech_backpressure');
    }
    const item: LiveOutputItem = {
      ...input,
      generationEpoch,
      outputOrder: this.nextOrder++,
      status: 'queued',
      delivery: {
        generatedTextEnd: 0,
        audioBufferedTextEnd: 0,
        audioDeliveredTextEnd: 0,
        visualDeliveredTextEnd: 0,
        contextDeliveredTextEnd: 0,
        ...input.delivery,
      },
    };
    this.items.set(item.outputId, item);
    if (!this.orderedIds.includes(item.outputId)) this.orderedIds.push(item.outputId);
    this.sort();
    return item;
  }

  transition(outputId: string, generationEpoch: number, status: LiveOutputStatus): LiveOutputItem {
    const item = this.requireCurrent(outputId, generationEpoch);
    const next = { ...item, status };
    this.items.set(outputId, next);
    return next;
  }

  updateDelivery(
    outputId: string,
    generationEpoch: number,
    delivery: Partial<LiveOutputDeliveryState>,
  ): LiveOutputItem {
    const item = this.requireCurrent(outputId, generationEpoch);
    const nextDelivery = { ...item.delivery, ...delivery };
    if (nextDelivery.contextDeliveredTextEnd > nextDelivery.audioDeliveredTextEnd
      && nextDelivery.contextDeliveredTextEnd > nextDelivery.visualDeliveredTextEnd) {
      throw new Error('context_delivery_exceeds_user_delivery');
    }
    const next = { ...item, delivery: nextDelivery };
    this.items.set(outputId, next);
    return next;
  }

  cancel(outputId: string, generationEpoch: number, reason: string): LiveOutputItem | null {
    const item = this.items.get(outputId);
    if (!item || item.generationEpoch !== generationEpoch || isTerminal(item.status)) return null;
    const next = { ...item, status: 'cancelled' as const, cancellationReason: reason };
    this.items.set(outputId, next);
    return next;
  }

  cancelAllAfter(outputId: string, reason: string): LiveOutputItem[] {
    const pivot = this.items.get(outputId);
    if (!pivot) return [];
    const cancelled: LiveOutputItem[] = [];
    for (const item of this.snapshot) {
      if (item.outputOrder <= pivot.outputOrder || isTerminal(item.status)) continue;
      const next = { ...item, status: 'cancelled' as const, cancellationReason: reason };
      this.items.set(item.outputId, next);
      cancelled.push(next);
    }
    return cancelled;
  }

  acceptsFrame(outputId: string, generationEpoch: number): boolean {
    const item = this.items.get(outputId);
    return Boolean(item && item.generationEpoch === generationEpoch && !isTerminal(item.status));
  }

  prune(): void {
    for (let index = this.orderedIds.length - 1; index >= 0; index -= 1) {
      const id = this.orderedIds[index];
      const item = this.items.get(id);
      if (!item || !isTerminal(item.status)) continue;
      this.orderedIds.splice(index, 1);
      this.items.delete(id);
    }
  }

  private requireCurrent(outputId: string, generationEpoch: number): LiveOutputItem {
    const item = this.items.get(outputId);
    if (!item) throw new Error('output_item_missing');
    if (item.generationEpoch !== generationEpoch) throw new Error('output_epoch_stale');
    return item;
  }

  private sort(): void {
    this.orderedIds.sort((leftId, rightId) => {
      const left = this.items.get(leftId)!;
      const right = this.items.get(rightId)!;
      return PRIORITY_ORDER[left.priority] - PRIORITY_ORDER[right.priority]
        || left.outputOrder - right.outputOrder;
    });
  }
}

export function createEmptyOutputDeliveryState(): LiveOutputDeliveryState {
  return {
    generatedTextEnd: 0,
    audioBufferedTextEnd: 0,
    audioDeliveredTextEnd: 0,
    visualDeliveredTextEnd: 0,
    contextDeliveredTextEnd: 0,
  };
}

function isTerminal(status: LiveOutputStatus): boolean {
  return status === 'completed' || status === 'cancelled' || status === 'failed';
}
