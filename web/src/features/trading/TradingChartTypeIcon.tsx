type TradingChartTypeIconProps = {
  kind: string;
  size?: number;
};

export function TradingChartTypeIcon({ kind, size = 25 }: TradingChartTypeIconProps) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.35,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  };

  switch (kind) {
    case 'bars':
      return <svg {...common}><path d="M12 2v20M4 7h8M12 17h8" /></svg>;
    case 'hollow-candles':
      return <svg {...common}><path d="M7 2v20M17 2v20" /><rect x="4.5" y="7" width="5" height="9" /><rect x="14.5" y="5" width="5" height="12" /></svg>;
    case 'volume-candles':
      return <svg {...common}><path d="M6 3v18M12 2v20M18 4v16" /><rect x="3.5" y="9" width="5" height="7" /><rect x="9.5" y="5" width="5" height="12" /><rect x="15.5" y="11" width="5" height="4" /></svg>;
    case 'candles':
    case 'heikin-ashi':
      return <svg {...common}><path d="M7 2v20M17 2v20" /><rect x="4.5" y="6" width="5" height="11" /><rect x="14.5" y="4" width="5" height="10" /></svg>;
    case 'line-with-markers':
      return <svg {...common}><path d="m2 17 6-7 5 4 9-10" /><circle cx="2" cy="17" r="1.7" fill="currentColor" /><circle cx="8" cy="10" r="1.7" fill="currentColor" /><circle cx="13" cy="14" r="1.7" fill="currentColor" /><circle cx="22" cy="4" r="1.7" fill="currentColor" /></svg>;
    case 'step-line':
      return <svg {...common}><path d="M2 18h5V9h5v5h5V5h5" /></svg>;
    case 'area':
      return <svg {...common}><path d="m2 17 6-7 5 4 9-10v15H2Z" fill="currentColor" opacity=".28" /><path d="m2 17 6-7 5 4 9-10" /></svg>;
    case 'hlc-area':
      return <svg {...common}><path d="m2 16 5-6 5 3 5-7 5 3v13H2Z" opacity=".2" /><path d="m2 16 5-6 5 3 5-7 5 3M2 19l5-5 5 3 5-5 5 2" /></svg>;
    case 'baseline':
      return <svg {...common}><path d="M2 16h20" strokeDasharray="2 2" /><path d="m2 11 5-4 4 6 5-8 6 5" /><path d="m2 16 5 2 4-1 5 2 6-2" opacity=".45" /></svg>;
    case 'columns':
      return <svg {...common}><path d="M3 21V11h4v10M10 21V5h4v16M17 21V9h4v12" /></svg>;
    case 'high-low':
      return <svg {...common}><path d="M6 3v18M18 5v14M3 8h6M15 16h6" /></svg>;
    case 'volume-footprint':
      return <svg {...common}><path d="M6 3v18M3 8h6M3 12h6M3 16h6M14 5h7M14 9h5M14 13h7M14 17h4" /></svg>;
    case 'time-price-opportunity':
      return <svg {...common}><path d="M3 3h18v18H3zM3 9h18M3 15h18M9 3v18M15 3v18" /></svg>;
    case 'session-volume-profile':
      return <svg {...common}><path d="M3 4h8v4H3zM3 10h14v4H3zM3 16h10v4H3zM11 4h10v4H11z" /></svg>;
    case 'renko':
      return <svg {...common}><path d="m3 17 5-5 5 5 5-5 3 3" /><rect x="1.5" y="15" width="5" height="5" /><rect x="6.5" y="10" width="5" height="5" /><rect x="11.5" y="15" width="5" height="5" /><rect x="16.5" y="10" width="5" height="5" /></svg>;
    case 'line-break':
      return <svg {...common}><path d="M3 19V8h5v7h5V5h5v10h3" /><path d="M3 19h5M13 5h5" /></svg>;
    case 'kagi':
      return <svg {...common}><path d="M4 20V7h6v6h5V3h5" /><path d="M10 7h5M15 13h5" opacity=".55" /></svg>;
    case 'point-figure':
      return <svg {...common}><circle cx="6" cy="6" r="2" /><circle cx="6" cy="12" r="2" /><circle cx="6" cy="18" r="2" /><path d="m15 4 4 4m0-4-4 4m0 4 4 4m0-4-4 4" /></svg>;
    case 'range':
      return <svg {...common}><path d="M12 3v18M5 7h14M5 17h14" /><path d="M8 7v10M16 7v10" opacity=".55" /></svg>;
    case 'line':
    default:
      return <svg {...common}><path d="m2 17 6-7 5 4 9-10" /></svg>;
  }
}
