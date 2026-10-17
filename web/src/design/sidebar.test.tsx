import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { OmnixBrand, OmnixSidebar } from './primitives';

describe('OmnixSidebar', () => {
  it('starts expanded and can collapse to the compact rail', () => {
    render(
      <OmnixSidebar>
        <OmnixBrand />
        <nav>Chat</nav>
      </OmnixSidebar>,
    );

    const sidebar = screen.getByLabelText('Omnix navigation');
    expect(sidebar).toHaveClass('expanded');
    expect(screen.getByLabelText('Collapse sidebar')).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(screen.getByLabelText('Collapse sidebar'));

    expect(sidebar).toHaveClass('collapsed');
    expect(screen.getByLabelText('Expand sidebar')).toHaveAttribute('aria-expanded', 'false');
  });
});
