/**
 * The editor's language support for Omnix Scripts (TVP-11.2): Pine-compatible highlighting, completion of the
 * interpreter's names (`/api/trading/scripts/reference`), hover with a function's parameters, and the server's
 * compile problems as inline diagnostics.
 */
import type { Completion, CompletionContext, CompletionResult } from '@codemirror/autocomplete';
import { StreamLanguage, type StringStream } from '@codemirror/language';
import type { Diagnostic } from '@codemirror/lint';
import type { Text } from '@codemirror/state';
import type { ScriptDiagnostic, ScriptReference } from './scriptsApi';
import { PRODUCT_NAME } from '../../../shared/productIdentity';

const KEYWORDS = new Set([
  'if', 'else', 'for', 'to', 'by', 'in', 'while', 'switch', 'var', 'varip', 'and', 'or', 'not', 'break', 'continue',
  'import', 'export', 'method', 'type', 'as',
]);
const TYPES = new Set(['int', 'float', 'bool', 'string', 'color', 'series', 'simple', 'const', 'array', 'map', 'matrix', 'line', 'label', 'box', 'table']);
const ATOMS = new Set(['true', 'false', 'na']);
/** Namespaces of built-in functions, constants and variables. */
const NAMESPACES = new Set([
  'ta', 'math', 'str', 'array', 'map', 'matrix', 'input', 'color', 'request', 'strategy', 'line', 'label', 'box', 'table',
  'linefill', 'barstate', 'syminfo', 'timeframe', 'shape', 'location', 'size', 'plot', 'hline', 'display', 'extend', 'xloc',
  'yloc', 'position', 'format', 'text', 'alert', 'log', 'session', 'currency', 'dayofweek', 'barmerge', 'chart', 'runtime',
]);
/** Top-level built-ins without a namespace. */
const BUILTINS = new Set([
  'indicator', 'strategy', 'library', 'plot', 'plotshape', 'plotchar', 'plotarrow', 'plotcandle', 'plotbar', 'bgcolor', 'barcolor',
  'hline', 'fill', 'alert', 'alertcondition', 'nz', 'na', 'fixnan', 'timestamp', 'time', 'time_close', 'year', 'month', 'dayofmonth',
  'hour', 'minute', 'second', 'weekofyear', 'open', 'high', 'low', 'close', 'volume', 'hl2', 'hlc3', 'ohlc4', 'hlcc4', 'bar_index',
  'last_bar_index', 'timenow', 'int', 'float', 'bool', 'string',
]);

type State = { afterDot: boolean };

function token(stream: StringStream, state: State): string | null {
  if (stream.eatSpace()) return null;
  if (stream.match('//')) {
    stream.skipToEnd();
    return 'comment';
  }
  const quote = stream.peek();
  if (quote === '"' || quote === "'") {
    stream.next();
    let escaped = false;
    for (let next = stream.next(); next !== undefined; next = stream.next()) {
      if (next === quote && !escaped) break;
      escaped = !escaped && next === '\\';
    }
    state.afterDot = false;
    return 'string';
  }
  if (stream.match(/^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b/)) return 'atom';
  if (stream.match(/^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/)) {
    state.afterDot = false;
    return 'number';
  }
  const word = stream.match(/^[A-Za-z_]\w*/) as RegExpMatchArray | null;
  if (word) {
    const name = word[0];
    const member = state.afterDot;
    state.afterDot = false;
    if (member) return 'property';
    if (stream.peek() === '.' && NAMESPACES.has(name)) return 'builtin';
    if (KEYWORDS.has(name)) return 'keyword';
    if (ATOMS.has(name)) return 'atom';
    if (TYPES.has(name) && /^\s+[A-Za-z_]/.test(stream.string.slice(stream.pos))) return 'type';
    if (BUILTINS.has(name)) return 'builtin';
    return 'variable';
  }
  if (stream.eat('.')) {
    state.afterDot = true;
    return null;
  }
  if (stream.match(/^(?:=>|:=|==|!=|<=|>=|\+=|-=|\*=|\/=|%=|[-+*/%<>=?:])/)) {
    state.afterDot = false;
    return 'operator';
  }
  stream.next();
  state.afterDot = false;
  return null;
}

/** The tokenizer, for tests: a fresh state and the style of the next token. */
export const scriptTokenizer = { startState: (): State => ({ afterDot: false }), token };

export const omnixScriptLanguage = StreamLanguage.define<State>({
  name: 'omnix-script',
  startState: scriptTokenizer.startState,
  token,
  languageData: { commentTokens: { line: '//' }, indentOnInput: /^\s*(?:else)$/ },
});

/** Completion options from the reference: functions (with their parameters), constants, variables and keywords. */
export function scriptCompletionOptions(reference: ScriptReference): Completion[] {
  const signatures = reference.signatures ?? {};
  return [
    ...reference.functions.map((name) => ({ label: name, type: 'function', detail: signatures[name] ? `(${signatures[name]})` : '()', boost: 1 })),
    ...reference.variables.map((name) => ({ label: name, type: 'variable' })),
    ...reference.constants.map((name) => ({ label: name, type: 'constant' })),
    ...reference.keywords.map((name) => ({ label: name, type: 'keyword', boost: -1 })),
  ];
}

/** A completion source over the reference's names, including dotted ones (`ta.s` offers `ta.sma`). */
export function scriptCompletionSource(options: () => readonly Completion[]) {
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/[A-Za-z_][\w.]*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;
    const line = context.state.doc.lineAt(context.pos);
    // Not inside a comment or a string on this line.
    const before = line.text.slice(0, word.from - line.from);
    if (before.includes('//') || (before.split('"').length - 1) % 2 === 1 || (before.split("'").length - 1) % 2 === 1) return null;
    return { from: word.from, options: options() as Completion[], validFor: /^[\w.]*$/ };
  };
}

/** The dotted name at a document position (`ta.sma` anywhere over it), or null. */
export function nameAt(doc: Text, pos: number): { from: number; to: number; name: string } | null {
  const line = doc.lineAt(pos);
  const offset = pos - line.from;
  const pattern = /[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*/g;
  for (let match = pattern.exec(line.text); match; match = pattern.exec(line.text)) {
    if (match.index <= offset && offset <= match.index + match[0].length) {
      return { from: line.from + match.index, to: line.from + match.index + match[0].length, name: match[0] };
    }
  }
  return null;
}

/** What hovering a name shows: a function with its parameters, or a constant or variable's kind. */
export function scriptHoverText(reference: ScriptReference, name: string): string | null {
  if (reference.functions.includes(name)) return `${name}(${reference.signatures?.[name] ?? '…'})`;
  if (reference.variables.includes(name)) return `${name}: built-in variable`;
  if (reference.constants.includes(name)) return `${name}: constant`;
  if (reference.keywords.includes(name)) return `${name}: keyword`;
  return null;
}

/** The server's problems as editor diagnostics (lines and columns from 1; a column of 0 marks the whole line). */
export function scriptDiagnostics(doc: Text, problems: readonly ScriptDiagnostic[]): Diagnostic[] {
  return problems.map((problem) => {
    const lineNumber = Math.min(Math.max(1, problem.line || 1), doc.lines);
    const line = doc.line(lineNumber);
    const column = problem.column > 0 ? Math.min(problem.column - 1, line.length) : 0;
    const from = line.from + column;
    const to = problem.column > 0 ? Math.min(line.to, from + Math.max(1, (/^\w+/.exec(line.text.slice(column))?.[0].length ?? 1))) : line.to;
    return { from, to: Math.max(to, from), severity: 'error', message: `${problem.kind === 'syntax' ? 'Syntax' : 'Error'}: ${problem.message}`, source: `${PRODUCT_NAME} Scripts` };
  });
}
