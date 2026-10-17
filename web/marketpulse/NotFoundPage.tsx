import { Link } from '@tanstack/react-router';

/** An address MarketPulse has no page for; Omnix's version links to Omnix's chat. */
export function NotFoundPage() {
  return (
    <div className="mp-startup" role="main">
      <img className="mp-startup-mark" src="/marketpulse-icon-128.png" alt="" width={64} height={64} />
      <h1>Page not found</h1>
      <Link to="/trading">Back to charts</Link>
    </div>
  );
}
