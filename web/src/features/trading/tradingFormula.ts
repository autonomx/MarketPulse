export type TradingFormulaOperator = '+' | '-' | '*' | '/' | '^';

export type TradingFormulaNode =
  | { kind: 'symbol'; value: string }
  | { kind: 'number'; value: number }
  | { kind: 'unary'; operator: '+' | '-'; operand: TradingFormulaNode }
  | { kind: 'binary'; operator: TradingFormulaOperator; left: TradingFormulaNode; right: TradingFormulaNode };

export type TradingFormula = {
  expression: string;
  root: TradingFormulaNode;
  symbols: string[];
};

export type TradingFormulaPayload = {
  expression: string;
  operands: Record<string, string>;
};

export type TradingFormulaParseOptions = {
  symbolHints?: Iterable<string>;
};

export const TRADING_FORMULA_INSTRUMENT_PREFIX = 'formula:';

type FormulaToken =
  | { kind: 'number'; value: number }
  | { kind: 'symbol'; value: string }
  | { kind: 'operator'; value: TradingFormulaOperator }
  | { kind: 'lparen' }
  | { kind: 'rparen' };

class FormulaSyntaxError extends Error {}

function isSymbolCharacter(value: string | undefined): boolean {
  return value !== undefined && /[A-Za-z0-9_.$:]/.test(value);
}

function tokenize(source: string, symbolHints: ReadonlySet<string>): FormulaToken[] {
  const tokens: FormulaToken[] = [];
  let index = 0;
  while (index < source.length) {
    const character = source[index];
    if (/\s/.test(character)) {
      index += 1;
      continue;
    }
    if (character === '(') {
      tokens.push({ kind: 'lparen' });
      index += 1;
      continue;
    }
    if (character === ')') {
      tokens.push({ kind: 'rparen' });
      index += 1;
      continue;
    }
    if ('+*/^'.includes(character)) {
      tokens.push({ kind: 'operator', value: character as TradingFormulaOperator });
      index += 1;
      continue;
    }
    if (character === '-') {
      tokens.push({ kind: 'operator', value: '-' });
      index += 1;
      continue;
    }
    if (/\d/.test(character)) {
      const match = source.slice(index).match(/^\d+(?:\.\d+)?/);
      if (!match) throw new FormulaSyntaxError('Invalid number');
      const value = Number(match[0]);
      if (!Number.isFinite(value)) throw new FormulaSyntaxError('Invalid number');
      tokens.push({ kind: 'number', value });
      index += match[0].length;
      continue;
    }
    if (isSymbolCharacter(character)) {
      const start = index;
      index += 1;
      while (index < source.length) {
        const candidate = source[index];
        if (isSymbolCharacter(candidate)) {
          index += 1;
          continue;
        }
        const previous = source[index - 1];
        const next = source[index + 1];
        if (candidate === '-' && isSymbolCharacter(previous) && isSymbolCharacter(next)) {
          const symbolEnd = source.slice(index + 1).search(/[^A-Za-z0-9_.$:-]/);
          const end = symbolEnd < 0 ? source.length : index + 1 + symbolEnd;
          if (symbolHints.has(source.slice(start, end).toUpperCase())) {
            index = end;
            continue;
          }
        }
        break;
      }
      const value = source.slice(start, index).toUpperCase();
      if (/^\d+(?:\.\d+)?$/.test(value)) tokens.push({ kind: 'number', value: Number(value) });
      else tokens.push({ kind: 'symbol', value });
      continue;
    }
    throw new FormulaSyntaxError(`Unsupported character '${character}'`);
  }
  return tokens;
}

function parseTokens(tokens: FormulaToken[]): TradingFormulaNode {
  let index = 0;
  const peek = () => tokens[index];
  const consume = () => tokens[index++];

  const primary = (): TradingFormulaNode => {
    const token = consume();
    if (!token) throw new FormulaSyntaxError('Expected a symbol or number');
    if (token.kind === 'number') return { kind: 'number', value: token.value };
    if (token.kind === 'symbol') return { kind: 'symbol', value: token.value };
    if (token.kind === 'lparen') {
      const node = additive();
      if (consume()?.kind !== 'rparen') throw new FormulaSyntaxError('Missing closing parenthesis');
      return node;
    }
    throw new FormulaSyntaxError('Expected a symbol or number');
  };

  const unary = (): TradingFormulaNode => {
    const token = peek();
    if (token?.kind === 'operator' && (token.value === '+' || token.value === '-')) {
      consume();
      return { kind: 'unary', operator: token.value, operand: unary() };
    }
    return primary();
  };

  const power = (): TradingFormulaNode => {
    const left = unary();
    const token = peek();
    if (token?.kind !== 'operator' || token.value !== '^') return left;
    consume();
    return { kind: 'binary', operator: '^', left, right: power() };
  };

  const multiplicative = (): TradingFormulaNode => {
    let node = power();
    while (true) {
      const token = peek();
      if (token?.kind !== 'operator' || (token.value !== '*' && token.value !== '/')) return node;
      consume();
      node = { kind: 'binary', operator: token.value, left: node, right: power() };
    }
  };

  function additive(): TradingFormulaNode {
    let node = multiplicative();
    while (true) {
      const token = peek();
      if (token?.kind !== 'operator' || (token.value !== '+' && token.value !== '-')) return node;
      consume();
      node = { kind: 'binary', operator: token.value, left: node, right: multiplicative() };
    }
  }

  const root = additive();
  if (index !== tokens.length) throw new FormulaSyntaxError('Unexpected formula token');
  return root;
}

function collectSymbols(node: TradingFormulaNode, symbols: string[]): void {
  if (node.kind === 'symbol') {
    if (!symbols.includes(node.value)) symbols.push(node.value);
    return;
  }
  if (node.kind === 'number') return;
  if (node.kind === 'unary') {
    collectSymbols(node.operand, symbols);
    return;
  }
  collectSymbols(node.left, symbols);
  collectSymbols(node.right, symbols);
}

/** Returns null for normal symbol queries and invalid arithmetic input. */
export function parseTradingFormula(input: string, options: TradingFormulaParseOptions = {}): TradingFormula | null {
  const trimmed = input.trim();
  if (!trimmed) return null;
  const explicit = trimmed.startsWith('=');
  const expression = (explicit ? trimmed.slice(1) : trimmed).trim();
  if (!expression || (!explicit && !/[+*/^-]/.test(expression))) return null;
  try {
    const symbolHints = new Set([...options.symbolHints ?? []].map((symbol) => symbol.trim().toUpperCase()));
    const tokens = tokenize(expression, symbolHints);
    if (!tokens.some((token) => token.kind === 'operator')) return null;
    const root = parseTokens(tokens);
    const symbols: string[] = [];
    collectSymbols(root, symbols);
    if (symbols.length === 0) return null;
    return { expression: expression.replace(/\s+/g, ' '), root, symbols };
  } catch {
    return null;
  }
}

export function evaluateTradingFormula(
  node: TradingFormulaNode,
  resolveSymbol: (symbol: string) => number | null,
): number | null {
  if (node.kind === 'number') return node.value;
  if (node.kind === 'symbol') {
    const value = resolveSymbol(node.value);
    return value !== null && Number.isFinite(value) ? value : null;
  }
  if (node.kind === 'unary') {
    const value = evaluateTradingFormula(node.operand, resolveSymbol);
    if (value === null) return null;
    return node.operator === '-' ? -value : value;
  }
  const left = evaluateTradingFormula(node.left, resolveSymbol);
  const right = evaluateTradingFormula(node.right, resolveSymbol);
  if (left === null || right === null) return null;
  if (node.operator === '/' && Math.abs(right) < Number.EPSILON) return null;
  const value = node.operator === '+'
    ? left + right
    : node.operator === '-'
      ? left - right
      : node.operator === '*'
        ? left * right
        : node.operator === '/'
          ? left / right
          : left ** right;
  return Number.isFinite(value) ? value : null;
}

export function encodeTradingFormula(expression: string, operands: Record<string, string>): string {
  const payload: TradingFormulaPayload = { expression, operands };
  return `${TRADING_FORMULA_INSTRUMENT_PREFIX}${encodeURIComponent(JSON.stringify(payload))}`;
}

export function decodeTradingFormula(instrumentId: string): TradingFormulaPayload | null {
  if (!instrumentId.startsWith(TRADING_FORMULA_INSTRUMENT_PREFIX)) return null;
  try {
    const parsed = JSON.parse(decodeURIComponent(instrumentId.slice(TRADING_FORMULA_INSTRUMENT_PREFIX.length))) as Partial<TradingFormulaPayload>;
    if (typeof parsed.expression !== 'string' || !parsed.operands || typeof parsed.operands !== 'object') return null;
    const operands = Object.fromEntries(
      Object.entries(parsed.operands).filter(([symbol, operand]) => typeof symbol === 'string' && typeof operand === 'string'),
    );
    return Object.keys(operands).length > 0 ? { expression: parsed.expression, operands } : null;
  } catch {
    return null;
  }
}

export function isTradingFormulaInstrumentId(instrumentId: string): boolean {
  return decodeTradingFormula(instrumentId) !== null;
}

export function tradingFormulaDisplaySymbol(instrumentId: string): string | null {
  return decodeTradingFormula(instrumentId)?.expression ?? null;
}
