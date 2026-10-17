import type { ReactNode } from 'react';
import type { SettingScope } from './settingsTypes';

export function SettingScopeBadge({ scope }: { scope: SettingScope }) {
  return <span className={`settings-scope-badge scope-${scope}`}>{scope.replace('-', ' ')}</span>;
}

export function SettingsSection({ title, description, scope, actions, children, className = '' }: {
  title: string;
  description?: string;
  scope?: SettingScope;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`settings-section ${className}`}>
      <header className="settings-section-header">
        <div>
          <h3>{title}</h3>
          {description ? <p>{description}</p> : null}
        </div>
        <div className="settings-section-actions">
          {scope ? <SettingScopeBadge scope={scope} /> : null}
          {actions}
        </div>
      </header>
      <div className="settings-section-body">{children}</div>
    </section>
  );
}

export function SettingsField({ label, help, error, children, wide = false }: {
  label: string;
  help?: string;
  error?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={`settings-field${wide ? ' settings-field-wide' : ''}`}>
      <span className="settings-field-label">{label}</span>
      {children}
      {error ? <span className="settings-field-error" role="alert">{error}</span> : help ? <span className="settings-field-help">{help}</span> : null}
    </label>
  );
}

export function SettingsStatusRow({ icon, label, value, tone = 'neutral', onClick }: {
  icon?: string;
  label: string;
  value: string;
  tone?: 'ready' | 'warning' | 'error' | 'idle' | 'neutral';
  onClick?: () => void;
}) {
  const content = (
    <>
      <span className="settings-status-icon" aria-hidden="true">{icon ?? '•'}</span>
      <span className="settings-status-label">{label}</span>
      <span className={`settings-status-value tone-${tone}`}><i aria-hidden="true" />{value}</span>
      <span aria-hidden="true">›</span>
    </>
  );
  return onClick ? <button className="settings-status-row" type="button" onClick={onClick}>{content}</button> : <div className="settings-status-row">{content}</div>;
}

export function SettingsAdvanced({ label, children }: { label: string; children?: ReactNode }) {
  return (
    <details className="settings-advanced">
      <summary>{label}</summary>
      {children ? <div className="settings-advanced-body">{children}</div> : null}
    </details>
  );
}
