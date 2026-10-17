import { useCallback, useEffect, useRef, useState } from 'react';

export interface ChapterPlaybackState {
  /** The chapter loaded in the player, playing or paused; null when stopped. */
  chapterId: string | null;
  playing: boolean;
  position: number;
  error: string | null;
}

const STOPPED: ChapterPlaybackState = { chapterId: null, playing: false, position: 0, error: null };

/** One audio player shared by every chapter: playing a chapter replaces whatever was loaded. */
export function useChapterPlayback() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const loadedRef = useRef<string | null>(null);
  const [state, setState] = useState<ChapterPlaybackState>(STOPPED);

  const stop = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    loadedRef.current = null;
    setState(STOPPED);
  }, []);

  const element = useCallback(() => {
    if (audioRef.current) return audioRef.current;
    const audio = new Audio();
    audio.preload = 'metadata';
    audio.addEventListener('timeupdate', () => setState((current) => ({ ...current, position: audio.currentTime })));
    audio.addEventListener('play', () => setState((current) => ({ ...current, playing: true, error: null })));
    audio.addEventListener('pause', () => setState((current) => ({ ...current, playing: false })));
    audio.addEventListener('ended', () => stop());
    audio.addEventListener('error', () => {
      if (!loadedRef.current) return; // clearing the source on stop is not a failure
      loadedRef.current = null;
      setState({ ...STOPPED, error: 'This chapter’s audio could not be played.' });
    });
    audioRef.current = audio;
    return audio;
  }, [stop]);

  const play = useCallback((chapterId: string, url: string) => {
    const audio = element();
    if (loadedRef.current !== chapterId) {
      loadedRef.current = chapterId;
      audio.src = url;
      setState({ chapterId, playing: false, position: 0, error: null });
    }
    const started = audio.play();
    started?.catch(() => setState((current) => ({ ...current, playing: false, error: 'Playback did not start.' })));
  }, [element]);

  const pause = useCallback(() => audioRef.current?.pause(), []);

  // Leaving the page stops playback and releases the stream.
  useEffect(() => () => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.removeAttribute('src');
    }
  }, []);

  return { ...state, play, pause, stop };
}

export type ChapterPlayback = ReturnType<typeof useChapterPlayback>;

export function formatPlaybackTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = String(whole % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`;
}
