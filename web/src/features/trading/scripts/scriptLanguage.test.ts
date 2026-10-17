import { CompletionContext } from '@codemirror/autocomplete';
import { StringStream } from '@codemirror/language';
import { EditorState, Text } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import { describe, expect, it } from 'vitest';
import { diffLines, diffStats } from './scriptDiff';
import { nameAt, omnixScriptLanguage, scriptTokenizer, scriptCompletionOptions, scriptCompletionSource, scriptDiagnostics, scriptHoverText } from './scriptLanguage';

const reference = {
  functions: ['ta.sma', 'plot'], constants: ['color.blue'], variables: ['close', 'bar_index'], keywords: ['if', 'var'],
  signatures: { 'ta.sma': 'source, length' },
};

function tokens(line: string): Array<[string, string | null]> {
  const parser = scriptTokenizer;
  const state = parser.startState();
  const stream = new StringStream(line, 2, 2);
  const out: Array<[string, string | null]> = [];
  while (!stream.eol()) {
    stream.start = stream.pos;
    const style = parser.token(stream, state);
    if (stream.current().trim()) out.push([stream.current(), style]);
  }
  return out;
}

describe('Omnix Scripts language (TVP-11.2)', () => {
  it('highlights keywords, built-ins, strings, numbers, colours and comments', () => {
    expect(tokens('var x = ta.sma(close, 14) // avg')).toEqual([
      ['var', 'keyword'], ['x', 'variable'], ['=', 'operator'], ['ta', 'builtin'], ['.', null], ['sma', 'property'], ['(', null], ['close', 'builtin'],
      [',', null], ['14', 'number'], [')', null], ['// avg', 'comment'],
    ]);
    expect(tokens('plot(na, "it\'s \\"x\\"", color=#FF000080)').map(([, style]) => style)).toEqual([
      'builtin', null, 'atom', null, 'string', null, 'variable', 'operator', 'atom', null,
    ]);
  });

  it('completes dotted names outside strings and comments', () => {
    const source = scriptCompletionSource(() => scriptCompletionOptions(reference));
    const at = (doc: string) => source(new CompletionContext(EditorState.create({ doc }), doc.length, false));
    expect(at('x = ta.s')).toMatchObject({ from: 4 });
    expect(at('x = ta.s')?.options.find((option) => option.label === 'ta.sma')).toMatchObject({ type: 'function', detail: '(source, length)' });
    expect(at('// ta.s')).toBeNull();
    expect(at('s = "ta.s')).toBeNull();
  });

  it('hovers a function with its parameters', () => {
    const doc = Text.of(['x = ta.sma(close, 14)']);
    expect(nameAt(doc, 7)).toEqual({ from: 4, to: 10, name: 'ta.sma' });
    expect(scriptHoverText(reference, 'ta.sma')).toBe('ta.sma(source, length)');
    expect(scriptHoverText(reference, 'close')).toBe('close: built-in variable');
    expect(scriptHoverText(reference, 'mine')).toBeNull();
  });

  it('places the server problems on their line and column', () => {
    const doc = Text.of(['//@version=6', 'x = nosuch(close)', 'y = (']);
    const [named, whole] = scriptDiagnostics(doc, [
      { kind: 'name', message: 'unknown nosuch', line: 2, column: 5 },
      { kind: 'syntax', message: 'unexpected end', line: 9, column: 0 },
    ]);
    expect(doc.sliceString(named.from, named.to)).toBe('nosuch');
    expect(whole).toMatchObject({ from: doc.line(3).from, to: doc.line(3).to, message: 'Syntax: unexpected end' });
  });

  it('creates an editor in the page', () => {
    const parent = document.createElement('div');
    const view = new EditorView({ parent, state: EditorState.create({ doc: 'plot(close)', extensions: [omnixScriptLanguage] }) });
    expect(parent.querySelector('.cm-content')?.textContent).toBe('plot(close)');
    view.destroy();
  });
});

describe('script diff (TVP-11.3)', () => {
  it('keeps common lines and marks added and removed ones', () => {
    const lines = diffLines('a\nb\nc\nd', 'a\nc\nx\nd');
    expect(lines.map((line) => `${line.kind[0]}${line.text}`)).toEqual(['sa', 'rb', 'sc', 'ax', 'sd']);
    expect(lines.find((line) => line.text === 'x')).toMatchObject({ before: null, after: 3 });
    expect(diffStats(lines)).toEqual({ added: 1, removed: 1 });
    expect(diffLines('same', 'same')).toEqual([{ kind: 'same', text: 'same', before: 1, after: 1 }]);
  });
});
