import { describe, expect, it } from 'vitest';
import { unlayeredCss } from './unlayeredCss';

describe('unlayeredCss', () => {
  it('unwraps layer blocks, nested at-rules included, and drops layer statements', () => {
    const css = '@layer base, features;\n@layer base { a { color: red; } }\n@layer features { @media (min-width: 1px) { .b { color: blue; } } }\n.c { color: green; }';
    const out = unlayeredCss(css);

    expect(out).not.toContain('@layer');
    expect(out).toContain('a { color: red; }');
    expect(out).toContain('@media (min-width: 1px) { .b { color: blue; } }');
    expect(out.indexOf('a {')).toBeLessThan(out.indexOf('.b {'));
    expect(out).toContain('.c { color: green; }');
  });
});
