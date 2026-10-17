import { render, waitFor } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { VirtualList } from './VirtualList';

type Message = { id: string; text: string };
const messages = (count: number): Message[] => Array.from({ length: count }, (_, index) => ({ id: `m${index}`, text: `message ${index}` }));

function Transcript({ items, renders }: { items: Message[]; renders: string[] }) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  return (
    <div ref={scrollRef} data-scroll-viewport style={{ height: 600, overflow: 'auto' }}>
      <VirtualList
        items={items}
        getKey={(item) => item.id}
        scrollRef={scrollRef}
        renderItem={(item) => {
          renders.push(item.id);
          return <article>{item.text}</article>;
        }}
      />
    </div>
  );
}

beforeEach(() => {
  // jsdom has no layout: the viewport is 600px tall and every message 40px.
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.hasAttribute('data-scroll-viewport') ? 600 : 40;
  });
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(800);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('VirtualList', () => {
  it('renders a bounded number of rows for a 5,000 message transcript', async () => {
    const renders: string[] = [];
    const { container } = render(<Transcript items={messages(5_000)} renders={renders} />);

    await waitFor(() => expect(container.querySelectorAll('article').length).toBeGreaterThan(0));
    const rows = container.querySelectorAll('article');
    expect(rows.length).toBeLessThan(40);
    expect(new Set(renders).size).toBeLessThan(40);
    // The spacer keeps the full scroll height so the scrollbar spans every message.
    expect(parseInt((container.querySelector('.omnix-virtual-list') as HTMLElement).style.height, 10)).toBeGreaterThan(5_000 * 40 - 1);
  });

  it('renders every row of a short list so DOM readers still find them', () => {
    const { container } = render(<Transcript items={messages(20)} renders={[]} />);
    expect(container.querySelectorAll('article')).toHaveLength(20);
    expect(container.querySelector('.omnix-virtual-list')).toBeNull();
  });
});
