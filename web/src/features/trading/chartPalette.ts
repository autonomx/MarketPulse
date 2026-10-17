// Chart, drawing and indicator colours (WP-9.8). The chart library paints on a
// canvas and indicator settings store colours, so these stay literal values,
// named once here instead of repeated across components.
export const chartPalette = {
  yellow: '#ffd43b',
  orange: '#ff922b',
  amber: '#ff9f43',
  alertOrange: '#ff8f3d',
  red: '#ff6b6b',
  loss: '#ec4b5d',
  pink: '#e64980',
  orchid: '#e599f7',
  lavender: '#a875d4',
  violet: '#9775fa',
  blue: '#4dabf7',
  sky: '#74c0fc',
  paleSky: '#a5d8ff',
  cyan: '#66d9e8',
  drawingBlue: '#2962ff',
  teal: '#20c997',
  green: '#40ad50',
  gain: '#2fb879',
  slate: '#78899b',
  tealWash: 'rgba(32, 201, 151, .18)',
  amberWash: 'rgba(255, 159, 67, .18)',
  slateOverlay: 'rgba(109, 126, 143, .72)',
  paneBorder: 'rgba(117,151,181,.2)',
  paneBackground: 'rgba(7,16,27,.92)',
  paneShadow: 'rgba(0,0,0,.28)',
} as const;

export type ChartPaletteColor = (typeof chartPalette)[keyof typeof chartPalette];
