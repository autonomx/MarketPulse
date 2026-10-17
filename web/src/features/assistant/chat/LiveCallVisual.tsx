import { useEffect, useRef } from 'react';
import {
  avatarBackgroundImage,
  avatarCaption,
  avatarFrameAsset,
  avatarPresentationState,
  characterAvatarAssetUrl,
  useLiveAvatar,
} from './liveCharacterAvatarBridge';
import { LiveVoiceOrb } from './LiveVoiceOrb';
import { CHARACTER_LIVE2D_RENDER_EVENT, emitOmnixEvent } from '../../../events/bus';


/**
 * The live call's visual: the character avatar when the session has one (a
 * sprite frame, or a host for the Live2D renderer), the voice orb otherwise.
 */
export function LiveCallVisual({ voiceMode, thinking }: { voiceMode: string; thinking: boolean }) {
  const avatar = useLiveAvatar();
  const runtime = avatar.runtime;
  const pack = runtime?.avatar_pack ?? null;
  const live2d = pack?.renderer === 'live2d' && Boolean(pack.rig_asset_id);
  const state = avatarPresentationState(avatar.mouthFrame, voiceMode, thinking);
  const assetId = live2d ? '' : avatarFrameAsset(pack, avatar, state);
  const showAvatar = Boolean(runtime && (live2d || assetId));
  const live2dHostRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!live2d || !runtime || !live2dHostRef.current) return;
    emitOmnixEvent(CHARACTER_LIVE2D_RENDER_EVENT, { runtime, host: live2dHostRef.current });
  }, [live2d, runtime]);

  return (
    <div
      key={live2d && pack ? `${pack.character_id}:${pack.version}:${pack.rig_asset_id}` : 'voice-orb'}
      className="assistant-live-visual-stage"
      role="img"
      aria-label="Live character visual"
      data-has-character-avatar={showAvatar ? 'true' : undefined}
    >
      {showAvatar && runtime ? (
        live2d ? (
          <figure ref={live2dHostRef} className="assistant-live-character-avatar" data-renderer="live2d" data-mouth-frame={avatar.mouthFrame} data-voice-mode={state} />
        ) : (
          <figure className="assistant-live-character-avatar" data-renderer="sprite" data-mouth-frame={avatar.mouthFrame} data-voice-mode={state} style={{ backgroundImage: avatarBackgroundImage(pack) || undefined }}>
            <img src={characterAvatarAssetUrl(assetId)} alt={`${runtime.display_name} live avatar`} />
            <figcaption>{avatarCaption(runtime.display_name, state)}</figcaption>
          </figure>
        )
      ) : (
        <LiveVoiceOrb voiceMode={voiceMode} />
      )}
    </div>
  );
}
