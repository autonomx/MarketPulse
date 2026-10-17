import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ShapeElement } from './svgShapes';

describe('SVG drawing shapes', () => {
  it('keeps a text shape\'s own size and colour over the overlay stylesheet', () => {
    const { container } = render(
      <svg>
        <ShapeElement shape={{ kind: 'text', x: 1, y: 2, text: '🚀', fontSize: 48, fill: '#ffffff', stroke: 'none' }} index={0} />
        <ShapeElement shape={{ kind: 'text', x: 1, y: 2, text: 'plain' }} index={1} />
      </svg>,
    );
    const [emoji, plain] = Array.from(container.querySelectorAll('text'));
    expect(emoji.style.fontSize).toBe('48px');
    expect(emoji.style.fill).toBe('#ffffff');
    expect(emoji.style.stroke).toBe('none');
    expect(plain.getAttribute('style')).toBeNull();
  });

  it('drops the halo of text on its own box, over any theme (TVP-3.4)', () => {
    const { container } = render(
      <svg>
        <ShapeElement shape={{ kind: 'text', x: 1, y: 2, text: '69300.00', fill: '#ffffff', halo: false }} index={0} />
      </svg>,
    );
    expect(container.querySelector('text')!.style.stroke).toBe('none');
  });
});
