import type { HermesRpgApprovedFlowResponse } from './api/hermesRpgApprovedFlowClient';
import type { HermesRpgSuggestion } from './api/hermesClient';
import type {
  RpgGearPreview,
  RpgHeroSummaryPreview,
  RpgPartyMemberPreview,
  RpgQuestPreview,
  RpgStatPreview,
  RpgSurvivalPreview,
} from './rpgUiState';
import './RpgVisualAssets.css';

const HERO_ART_SRC = '/rpg/hero-alyndra.svg';
const RPG_PARTY_CAPACITY = 4;

type RpgRailPanelState = 'idle' | 'loading' | 'ready' | 'error' | 'empty';

interface RpgTurnReadoutPreview {
  category?: string;
  systems?: string[];
  effectCount?: number;
  groundingStatus?: string;
}

interface RpgRouteDecisionPreview {
  mode: string;
  hermesRole: string;
  owner: string;
  reviewRequired: boolean;
  boundary: string;
}

interface RpgPlayerRailProps {
  activeQuests: RpgQuestPreview[];
  className?: string;
  equippedGear: RpgGearPreview[];
  heroStats: RpgStatPreview[];
  heroSummary: RpgHeroSummaryPreview;
  hermesRouteDecision?: RpgRouteDecisionPreview;
  hermesRouteDecisionState?: RpgRailPanelState;
  hermesSuggestionFreshnessLabel?: string;
  hermesSuggestionState?: RpgRailPanelState;
  hermesSuggestions?: HermesRpgSuggestion[];
  hermesTurnReadout?: RpgTurnReadoutPreview;
  hermesTurnReadoutFreshnessLabel?: string;
  hermesTurnReadoutState?: RpgRailPanelState;
  onApprovedFlowAccepted?: (result: HermesRpgApprovedFlowResponse) => void | Promise<void>;
  onSelectCommand?: (command: string) => void;
  partyMembers: RpgPartyMemberPreview[];
  survival: RpgSurvivalPreview;
}

export function RpgPlayerRail(props: RpgPlayerRailProps) {
  const {
    activeQuests,
    className,
    equippedGear,
    heroStats,
    heroSummary,
    onSelectCommand,
    partyMembers,
    survival,
  } = props;
  const railClassName = className ? `rpg-left-rail ${className}` : 'rpg-left-rail';
  const heroAvatarClassName = heroSummary.source === 'preview'
    ? 'rpg-avatar rpg-hero-avatar rpg-hero-avatar-art'
    : 'rpg-avatar rpg-hero-avatar';

  return (
    <aside className={railClassName} aria-label="Player, party, and quests">
      <section className="rpg-card rpg-hero-card">
        <p className="eyebrow">Your hero</p>
        <div className="rpg-hero-summary">
          <div className={heroAvatarClassName} aria-hidden="true">
            {heroSummary.source === 'preview' ? <img src={HERO_ART_SRC} alt="" loading="lazy" /> : heroSummary.avatar}
          </div>
          <div>
            <h3>{heroSummary.name}</h3>
            <p>{heroSummary.subtitle}</p>
            <p>{heroSummary.origin}</p>
          </div>
        </div>
        <div className="rpg-stat-stack">
          {heroStats.map((stat) => (
            <div className="rpg-stat-row" key={stat.label}>
              <span>{stat.label}</span>
              <strong>{stat.value}</strong>
              <span className={`rpg-meter rpg-meter-${stat.tone}`} role="img" aria-label={`${stat.label} ${stat.value}`}>
                <span style={{ width: `${stat.percent}%` }} />
              </span>
            </div>
          ))}
          <div className="rpg-stat-row">
            <span>XP</span>
            <strong>{heroSummary.xpLabel}</strong>
            <span className="rpg-meter rpg-meter-xp" role="img" aria-label={`XP ${heroSummary.xpLabel}`}>
              <span style={{ width: `${heroSummary.xpPercent}%` }} />
            </span>
          </div>
        </div>
        <div className="rpg-resource-grid">
          <div>
            <span>Gold</span>
            <strong>{heroSummary.gold}</strong>
          </div>
          <div>
            <span>Renown</span>
            <strong>{heroSummary.renown}</strong>
          </div>
        </div>
      </section>

      <section className="rpg-card rpg-survival-card" aria-label="Survival status">
        <div className="rpg-section-heading">
          <p className="eyebrow">Survival</p>
          <span className={`rpg-survival-status is-${survival.status.toLowerCase()}`}>{survival.status}</span>
        </div>
        <p className="rpg-survival-detail">{survival.detail}</p>
        <div className="rpg-survival-needs">
          {survival.needs.map((need) => (
            <div className="rpg-survival-need" key={need.id}>
              <span>{need.label}</span>
              <strong>{need.value}</strong>
              <span
                role="img"
                aria-label={`${need.label} pressure ${need.value}`}
                className={`rpg-survival-meter is-${need.severity}`}
              >
                <span style={{ width: `${need.percent}%` }} />
              </span>
            </div>
          ))}
        </div>
        {survival.warnings.length ? (
          <div role="group" className="rpg-survival-warnings" aria-label="Survival warnings">
            {survival.warnings.map((warning) => <span key={warning}>{warning}</span>)}
          </div>
        ) : null}
        <div role="group" className="rpg-survival-actions" aria-label="Survival actions">
          {survival.actions.map((action) => (
            <button
              className="rpg-secondary-button"
              disabled={!onSelectCommand}
              key={action.command}
              onClick={() => onSelectCommand?.(action.command)}
              type="button"
            >
              {action.label}
            </button>
          ))}
        </div>
        <small>Actions prepare a deterministic turn command.</small>
      </section>

      <section className="rpg-card" aria-label="Active quests">
        <div className="rpg-section-heading">
          <p className="eyebrow">Objectives</p>
          <span>{activeQuests.length}</span>
        </div>
        <div className="rpg-list-stack">
          {activeQuests.map((quest) => (
            <article className="rpg-list-row" key={quest.title}>
              <span className="rpg-icon-tile" aria-hidden="true">{quest.icon}</span>
              <div>
                <strong>{quest.title}</strong>
                <span>{quest.detail}</span>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="rpg-card" aria-label="Party members">
        <div className="rpg-section-heading">
          <p className="eyebrow">Party</p>
          <span>{partyMembers.length} / {RPG_PARTY_CAPACITY}</span>
        </div>
        <div className="rpg-list-stack">
          {partyMembers.map((member) => (
            <article className="rpg-list-row" key={member.name}>
              <span className="rpg-icon-tile" aria-hidden="true">{member.avatar}</span>
              <div>
                <strong>{member.name}</strong>
                <span>{member.role}</span>
              </div>
              <span className="rpg-pill">{member.hp}</span>
            </article>
          ))}
        </div>
        <button
          className="rpg-secondary-button"
          disabled={!onSelectCommand || partyMembers.length >= RPG_PARTY_CAPACITY}
          onClick={() => onSelectCommand?.('Ask a trusted companion to join the party.')}
          type="button"
        >
          + Add companion
        </button>
      </section>

      <section className="rpg-card" aria-label="Equipped gear">
        <div className="rpg-section-heading">
          <p className="eyebrow">Gear</p>
          <span>{equippedGear.length}</span>
        </div>
        <div className="rpg-list-stack">
          {equippedGear.map((item) => (
            <article className="rpg-list-row" key={item.name}>
              <span className="rpg-icon-tile" aria-hidden="true">{item.icon}</span>
              <div>
                <strong>{item.name}</strong>
                <span>{item.slot}</span>
              </div>
            </article>
          ))}
        </div>
      </section>
    </aside>
  );
}
