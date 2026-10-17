// jsdom ignores rules inside `@layer` blocks. Tests that inject a stylesheet to
// read computed styles unwrap the layers first; the rules keep their order.
export function unlayeredCss(css: string): string {
  let out = '';
  let index = 0;
  const layerBlock = /@layer[^{;]*\{/g;
  for (let match = layerBlock.exec(css); match; match = layerBlock.exec(css)) {
    out += css.slice(index, match.index);
    let depth = 1;
    let end = layerBlock.lastIndex;
    for (; end < css.length && depth > 0; end += 1) {
      if (css[end] === '{') depth += 1;
      else if (css[end] === '}') depth -= 1;
    }
    out += unlayeredCss(css.slice(layerBlock.lastIndex, end - 1));
    index = end;
    layerBlock.lastIndex = end;
  }
  return (out + css.slice(index)).replace(/@layer[^{;]*;/g, '');
}
