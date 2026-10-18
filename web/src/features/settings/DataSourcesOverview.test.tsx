import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DataSourcesOverview } from './DataSourcesOverview';

describe('data sources overview', () => {
  it('says what each keyed and built-in source feeds', () => {
    const { container } = render(<DataSourcesOverview />);

    expect(screen.getByRole('heading', { name: 'What each data source is for' })).toBeInTheDocument();
    const [keyed, builtIn] = Array.from(container.querySelectorAll('dl')) as HTMLElement[];
    expect(within(keyed).getAllByRole('term').map((term) => term.textContent)).toEqual(['Alpaca', 'CoinMarketCap', 'FRED', 'Interactive Brokers']);
    expect(within(keyed).getByText(/option chains/)).toBeInTheDocument();
    expect(within(builtIn).getByText('Yahoo Finance')).toBeInTheDocument();
    expect(within(builtIn).getByText('SEC EDGAR')).toBeInTheDocument();
  });
});
