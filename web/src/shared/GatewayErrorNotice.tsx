/** Says why a workspace's data is missing when the gateway could not provide it (WP-9.10). */
export function GatewayErrorNotice({ label, errors }: { label: string; errors: unknown[] }) {
  const error = errors.find(Boolean);
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <p className="platform-empty" role="alert">{label} could not be loaded: {message}</p>;
}
