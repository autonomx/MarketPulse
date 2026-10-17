import { useMemo, useState } from 'react';
import type {
  RpgScenarioSummary,
  RpgWorldCampaignSummary,
  RpgWorldSummary,
} from './api/rpgWorldLibraryClient';
import { RpgWorldCard } from './RpgWorldCard';
import './RpgWorldCampaignCatalog.css';

interface RpgWorldCampaignCatalogProps {
  campaigns: RpgWorldCampaignSummary[];
  error?: string;
  isLoading: boolean;
  onBack: () => void;
  onContinueCampaign: (campaignId: string) => void;
  onNewCampaign: (worldId: string) => void;
  scenarios: RpgScenarioSummary[];
  worlds: RpgWorldSummary[];
}

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function campaignsForWorld(campaigns: RpgWorldCampaignSummary[], worldId: string) {
  return campaigns
    .filter((campaign) => campaign.world_id === worldId && campaign.status !== 'archived')
    .sort((left, right) => timestamp(right.updated_at) - timestamp(left.updated_at));
}

export function RpgWorldCampaignCatalog({
  campaigns,
  error,
  isLoading,
  onBack,
  onContinueCampaign,
  onNewCampaign,
  scenarios,
  worlds,
}: RpgWorldCampaignCatalogProps) {
  const [search, setSearch] = useState('');
  const [selectedCampaigns, setSelectedCampaigns] = useState<Record<string, string>>({});
  const visibleWorlds = useMemo(() => {
    const query = search.trim().toLowerCase();
    return worlds
      .filter((world) => world.status !== 'archived')
      .filter((world) => !query || [world.title, world.description, world.genre, world.tone]
        .some((value) => value.toLowerCase().includes(query)))
      .sort((left, right) => timestamp(right.updated_at) - timestamp(left.updated_at));
  }, [search, worlds]);

  return (
    <section className="rpg-world-catalog" aria-label="Available campaign worlds">
      <div className="rpg-world-catalog-heading">
        <div>
          <p className="eyebrow">Reusable worlds</p>
          <h4>Choose a World</h4>
          <p>Select a world to continue one of its campaigns or configure a new campaign from a published opening.</p>
        </div>
        <button className="rpg-secondary-button" type="button" onClick={onBack}>Back</button>
      </div>

      <label className="rpg-world-catalog-search">
        <span>Search worlds</span>
        <input
          aria-label="Search worlds"
          placeholder="Search by title, genre, or tone…"
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
        />
      </label>

      {isLoading ? <p className="rpg-world-catalog-message">Loading reusable worlds…</p> : null}
      {error ? <p className="rpg-world-catalog-error">{error}</p> : null}
      {!isLoading && !error && !visibleWorlds.length ? (
        <p className="rpg-world-catalog-message">
          No reusable worlds match this search. Create or import one from Worlds &amp; Campaigns.
        </p>
      ) : null}

      <div className="rpg-world-card-grid">
        {visibleWorlds.map((world) => {
          const worldCampaigns = campaignsForWorld(campaigns, world.id);
          const selectedCampaignId = selectedCampaigns[world.id] || worldCampaigns[0]?.campaign_id || '';
          const openingCount = scenarios.filter((scenario) => (
            scenario.world_id === world.id && scenario.status === 'published'
          )).length;
          return (
            <RpgWorldCard
              key={world.id}
              world={world}
              facts={(
                <>
                  <span>{openingCount} published opening{openingCount === 1 ? '' : 's'}</span>
                  <span>{worldCampaigns.length} campaign{worldCampaigns.length === 1 ? '' : 's'}</span>
                </>
              )}
              actions={(
                <>
                  <button
                    aria-label={`Continue campaign in ${world.title}`}
                    className="rpg-secondary-button"
                    type="button"
                    disabled={!selectedCampaignId}
                    onClick={() => selectedCampaignId && onContinueCampaign(selectedCampaignId)}
                  >
                    Continue
                  </button>
                  <button
                    aria-label={`New campaign in ${world.title}`}
                    className="rpg-primary-button"
                    type="button"
                    onClick={() => onNewCampaign(world.id)}
                  >
                    {openingCount < 1 ? 'Review Setup' : 'New Campaign'}
                  </button>
                </>
              )}
              footer={openingCount < 1 ? <small>Publish a scenario before creating a campaign.</small> : null}
            >
              {worldCampaigns.length ? (
                <label className="rpg-world-card-campaign-select">
                  <span>Existing campaign</span>
                  <select
                    aria-label={`Existing campaigns for ${world.title}`}
                    value={selectedCampaignId}
                    onChange={(event) => setSelectedCampaigns((current) => ({
                      ...current,
                      [world.id]: event.currentTarget.value,
                    }))}
                  >
                    {worldCampaigns.map((campaign) => (
                      <option key={campaign.campaign_id} value={campaign.campaign_id}>
                        {campaign.title} · {campaign.status}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <p className="rpg-world-card-empty">No campaigns have started in this world.</p>
              )}
            </RpgWorldCard>
          );
        })}
      </div>
    </section>
  );
}
