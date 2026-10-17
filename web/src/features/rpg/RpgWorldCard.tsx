import type { ReactNode } from 'react';
import type { RpgWorldSummary } from './api/rpgWorldLibraryClient';
import './RpgWorldCampaignCatalog.css';

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function assetImageUrl(value: unknown): string | undefined {
  const assetId = text(value);
  return assetId ? `/api/assets/${encodeURIComponent(assetId)}/file` : undefined;
}

export function resolveWorldCoverImage(world: RpgWorldSummary): string | undefined {
  const metadata = world.metadata ?? {};
  const candidate = [
    metadata.cover_image_url,
    metadata.cover_url,
    metadata.hero_image_url,
    metadata.banner_url,
    metadata.image_url,
  ].map(text).find(Boolean);
  if (candidate && /^(?:https?:\/\/|\/|data:image\/)/i.test(candidate)) return candidate;
  return [
    metadata.cover_image_asset_id,
    metadata.hero_image_asset_id,
    metadata.thumbnail_asset_id,
  ].map(assetImageUrl).find(Boolean);
}

export function displayWorldGenre(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

interface RpgWorldCardProps {
  actions: ReactNode;
  children?: ReactNode;
  facts?: ReactNode;
  footer?: ReactNode;
  world: RpgWorldSummary;
}

export function RpgWorldCard({ actions, children, facts, footer, world }: RpgWorldCardProps) {
  const image = resolveWorldCoverImage(world);
  return (
    <article className="rpg-world-card" data-world-id={world.id}>
      <div
        className={image ? 'rpg-world-card-cover rpg-world-card-cover-image' : 'rpg-world-card-cover'}
        style={image ? {
          backgroundImage: `linear-gradient(180deg, color-mix(in srgb, var(--c-blue-130) 4%, transparent), color-mix(in srgb, var(--c-blue-130) 88%, transparent)), url(${JSON.stringify(image)})`,
        } : undefined}
      >
        <span className="rpg-world-card-genre">{displayWorldGenre(world.genre || 'World')}</span>
        <div className="rpg-world-card-cover-copy">
          <strong>{world.title}</strong>
          <span>{world.tone || 'Reusable campaign world'}</span>
        </div>
      </div>

      <div className="rpg-world-card-body">
        <div>
          <h5>{world.title}</h5>
          <p className="rpg-world-card-description">
            {world.description || 'A reusable world ready for authoring and campaign play.'}
          </p>
        </div>
        {facts ? <div className="rpg-world-card-facts">{facts}</div> : null}
        {children}
        <div className="rpg-world-card-actions">{actions}</div>
        {footer}
      </div>
    </article>
  );
}
