import { clearPaperNotifications, usePaperNotifications } from './paperNotifications';

const KIND_LABELS = { fill: 'Filled', partial: 'Partly filled', reject: 'Rejected', cancel: 'Cancelled', modify: 'Modified', expire: 'Expired', closed: 'Closed' } as const;

/** The terminal dock's Notifications tab (TVP-7.4): paper order fills, rejections, cancellations and expiries. */
export function TradingNotificationsLog() {
  const notifications = usePaperNotifications();
  if (notifications.length === 0) {
    return <div className="trading-dock-empty"><strong>No notifications yet</strong><span>Fills, rejections, cancellations and expiries of paper orders appear here.</span></div>;
  }
  return (
    <div className="trading-dock-table-scroll">
      <table className="trading-notifications-table">
        <thead><tr><th>Time</th><th>Event</th><th>Details</th></tr></thead>
        <tbody>
          {notifications.map((item) => (
            <tr key={item.id} className={item.kind}>
              <td>{new Date(item.at).toLocaleTimeString()}</td>
              <td>{KIND_LABELS[item.kind]}</td>
              <td>{item.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" onClick={clearPaperNotifications}>Clear notifications</button>
    </div>
  );
}
