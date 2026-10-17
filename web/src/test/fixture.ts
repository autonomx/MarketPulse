/**
 * A test fixture that carries only the fields a test reads. Response types
 * list every field the gateway always sends, so partial fixtures go through here.
 */
export function fixture<T>(value: unknown): T {
  return value as T;
}
