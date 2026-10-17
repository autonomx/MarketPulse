import { useVirtualizer } from '@tanstack/react-virtual';
import { Fragment, useEffect, useReducer, type ReactNode, type RefObject } from 'react';

type VirtualListProps<T> = {
  items: readonly T[];
  getKey: (item: T) => string;
  renderItem: (item: T) => ReactNode;
  /** The element that scrolls; the list renders inside it. */
  scrollRef: RefObject<HTMLElement | null>;
  /** Lists up to this length render every item, so DOM readers still find them. */
  threshold?: number;
  estimateSize?: number;
  overscan?: number;
};

/**
 * Renders a long list with only the visible rows (plus overscan) in the DOM.
 * Rows may have any height; each is measured once rendered.
 */
export function VirtualList<T>(props: VirtualListProps<T>) {
  if (props.items.length <= (props.threshold ?? 200)) {
    return <>{props.items.map((item) => <Fragment key={props.getKey(item)}>{props.renderItem(item)}</Fragment>)}</>;
  }
  return <VirtualRows {...props} />;
}

function VirtualRows<T>({ items, getKey, renderItem, scrollRef, estimateSize = 160, overscan = 6 }: VirtualListProps<T>) {
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateSize,
    getItemKey: (index) => getKey(items[index]),
    overscan,
  });
  // The scroll element is an ancestor, whose ref is attached after this component's
  // layout effects; one more render lets the virtualizer find and observe it.
  const [, rerender] = useReducer((count: number) => count + 1, 0);
  useEffect(rerender, [rerender]);
  return (
    <div className="omnix-virtual-list" style={{ position: 'relative', width: '100%', flexShrink: 0, height: virtualizer.getTotalSize() }}>
      {virtualizer.getVirtualItems().map((row) => (
        <div
          key={row.key}
          className="omnix-virtual-row"
          data-index={row.index}
          ref={virtualizer.measureElement}
          // A column flex row keeps the item's own align-self (chat bubbles sit left or right).
          style={{ position: 'absolute', top: 0, left: 0, width: '100%', display: 'flex', flexDirection: 'column', transform: `translateY(${row.start}px)` }}
        >
          {renderItem(items[row.index])}
        </div>
      ))}
    </div>
  );
}
