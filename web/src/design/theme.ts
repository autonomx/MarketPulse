import { createTheme } from '@mantine/core';

export const omnixTheme = createTheme({
  primaryColor: 'cyan',
  // White text on filled buttons needs 4.5:1 (WCAG AA); cyan 9 gives 5.3:1, cyan 8 only 4.3:1.
  primaryShade: 9,
  defaultRadius: 'sm',
  fontFamily: 'var(--omnix-font-family)',
  headings: {
    fontFamily: 'var(--omnix-font-family)',
    fontWeight: '700',
  },
  spacing: {
    xs: '0.375rem',
    sm: '0.625rem',
    md: '1rem',
    lg: '1.5rem',
    xl: '2rem',
  },
  radius: {
    xs: '0.25rem',
    sm: '0.5rem',
    md: '0.75rem',
    lg: '1rem',
    xl: '1.25rem',
  },
  components: {
    Button: {
      defaultProps: {
        radius: 'sm',
      },
    },
    Paper: {
      defaultProps: {
        radius: 'sm',
      },
    },
  },
});
