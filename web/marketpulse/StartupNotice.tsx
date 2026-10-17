/** A centred notice with the MarketPulse mark, for loading states outside the trading workspace. */
export function StartupNotice({ message }: { message: string }) {
  return (
    <div className="mp-startup" role="status">
      <img className="mp-startup-mark" src="/marketpulse-icon-128.png" alt="" width={64} height={64} />
      <p>{message}</p>
    </div>
  );
}
