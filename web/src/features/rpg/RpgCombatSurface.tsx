import type { RpgCombatSurfacePreview } from './rpgCombatState';
import './RpgCombatSurface.css';

interface RpgCombatSurfaceProps {
  combat: RpgCombatSurfacePreview;
  onSelectCommand: (command: string) => void;
}

export function RpgCombatSurface({ combat, onSelectCommand }: RpgCombatSurfaceProps) {
  return (
    <section className="rpg-card rpg-combat-surface" aria-label="Combat surface">
      <div className="rpg-section-heading">
        <div>
          <p className="eyebrow">Tactical combat</p>
          <h3>{combat.title}</h3>
        </div>
        <span className={combat.active ? 'rpg-combat-status rpg-combat-status-active' : 'rpg-combat-status'}>{combat.statusLabel}</span>
      </div>

      <div role="group" className="rpg-combat-overview" aria-label="Combat round and active actor">
        <article>
          <span>Round</span>
          <strong>{combat.roundLabel}</strong>
        </article>
        <article>
          <span>Active actor</span>
          <strong>{combat.activeActorLabel}</strong>
        </article>
        <article>
          <span>Source</span>
          <strong>{combat.source === 'live' ? 'Live encounter' : 'Preview fallback'}</strong>
        </article>
      </div>

      <div role="group" className="rpg-initiative-strip" aria-label="Initiative queue">
        <strong>Initiative</strong>
        {combat.initiativeQueue.length ? (
          <ol>
            {combat.initiativeQueue.map((actor) => (
              <li key={actor}>{actor}</li>
            ))}
          </ol>
        ) : (
          <p>No initiative queue yet.</p>
        )}
      </div>

      <div role="group" className="rpg-combat-grid" aria-label="Combatants">
        {combat.combatants.length ? (
          combat.combatants.map((combatant) => (
            <article className={`rpg-combatant-card rpg-combatant-${combatant.tone}`} key={`${combatant.tone}:${combatant.name}`}>
              <div>
                <strong>{combatant.name}</strong>
                <small>{combatant.role}</small>
              </div>
              <span role="img" className="rpg-party-health" aria-label={`${combatant.name} health`}>
                <span style={{ width: `${combatant.hpPercent}%` }} />
              </span>
              <small>
                {combatant.hpLabel} • {combatant.status}
              </small>
            </article>
          ))
        ) : (
          <article className="rpg-combatant-card rpg-combatant-neutral">
            <strong>No enemy cards</strong>
            <small>Enemy cards appear when the live session exposes active encounter data.</small>
          </article>
        )}
      </div>

      <div role="group" className="rpg-combat-actions" aria-label="Combat actions">
        {combat.actions.map((action) => (
          <button
            className="rpg-combat-action-button"
            disabled={action.disabled}
            key={action.label}
            onClick={() => onSelectCommand(action.command)}
            title={action.reason ?? action.command}
            type="button"
          >
            <span aria-hidden="true">{action.icon}</span>
            {action.label}
          </button>
        ))}
      </div>

      <div role="group" className="rpg-combat-deltas" aria-label="Combat result deltas">
        <strong>Result deltas</strong>
        <ul>
          {combat.resultDeltas.map((delta) => (
            <li key={delta}>{delta}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
