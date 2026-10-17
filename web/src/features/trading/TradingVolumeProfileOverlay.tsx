import { useCallback, useLayoutEffect, useMemo, useRef } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import type { IndicatorOutput, VolumeProfileData } from './indicators/coreIndicators';
import './TradingVolumeProfileOverlay.css';

type TradingVolumeProfileOverlayProps = {
  adapter: TradingChartAdapter;
  outputs: readonly IndicatorOutput[];
};

type VolumeProfileRenderData = {
  key: string;
  profile: VolumeProfileData;
};

export function TradingVolumeProfileOverlay({ adapter, outputs }: TradingVolumeProfileOverlayProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const profile = useMemo<VolumeProfileRenderData | null>(() => {
    const output = outputs.find((item) => item.volumeProfile);
    return output?.volumeProfile ? { key: output.key, profile: output.volumeProfile } : null;
  }, [outputs]);

  const refresh = useCallback(() => {
    const svg = svgRef.current;
    if (!svg || !profile) return;
    const plotWidth = adapter.indicatorPlotWidth();
    const maxBarWidth = Math.min(180, Math.max(72, plotWidth * 0.24));
    for (const bar of svg.querySelectorAll<SVGRectElement>('[data-volume-profile-bin]')) {
      const index = Number(bar.dataset.volumeProfileBin);
      const bin = profile.profile.bins[index];
      if (!bin) continue;
      const upper = adapter.indicatorValueToCoordinate(profile.key, bin.high);
      const lower = adapter.indicatorValueToCoordinate(profile.key, bin.low);
      if (upper === null || lower === null) {
        bar.setAttribute('visibility', 'hidden');
        continue;
      }
      const width = profile.profile.maxVolume > 0
        ? maxBarWidth * Math.max(0, Math.min(1, bin.volume / profile.profile.maxVolume))
        : 0;
      bar.setAttribute('x', String(plotWidth - width));
      bar.setAttribute('y', String(Math.min(upper, lower)));
      bar.setAttribute('width', String(width));
      bar.setAttribute('height', String(Math.max(1, Math.abs(lower - upper) - 1)));
      bar.setAttribute('visibility', width > 0 ? 'visible' : 'hidden');
    }
  }, [adapter, profile]);

  useLayoutEffect(() => {
    refresh();
    return adapter.onViewportChange(refresh);
  }, [adapter, refresh]);

  if (!profile) return null;
  const valueAreaHigh = profile.profile.valueAreaHigh;
  const valueAreaLow = profile.profile.valueAreaLow;
  return (
    <svg ref={svgRef} className="trading-volume-profile-overlay" aria-hidden="true">
      {profile.profile.bins.map((bin, index) => {
        const inValueArea = bin.high > valueAreaLow && bin.low < valueAreaHigh;
        return (
          <rect
            key={`${bin.low}-${bin.high}`}
            data-volume-profile-bin={index}
            className={index === profile.profile.pocIndex ? 'is-poc' : inValueArea ? 'is-value-area' : ''}
            x="0"
            y="0"
            width="0"
            height="0"
            rx="1"
          />
        );
      })}
    </svg>
  );
}
