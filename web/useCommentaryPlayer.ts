"use client";

// Hook that orchestrates the commentary player over a source <video>.
//
// Owns: an off-DOM <audio> element holding the commentary WAV, plus the
// state derived from the video's currentTime — which segment is "active",
// whether an inline visualization should fire, and the source-volume
// duck level. Exposed to CommentaryToggle / InlineVisualization /
// AvatarOverlay so each can render without re-implementing the sync.
//
// The multi-track audio pattern (separate <Audio> elements; volume ducked
// when a commentary track is present) is the same pattern used in
// StudioOS-v1's Remotion DirectorShort composition — re-implemented here
// for an in-browser <video>+<audio> pair instead of Remotion.
//
// Reference (read-only): R:\--CODE--\StudioOS-v1\remotion\src\compositions\DirectorShort.tsx

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CommentSegment } from "./types";

/** How much we duck the source video's audio while commentary plays. */
export const DUCK_VOLUME = 0.5;
/** Default seconds an inline visualization plays before returning to source. */
const DEFAULT_VIS_DURATION_SEC = 4;

export type UseCommentaryPlayerArgs = {
  /** The source <video> ref the page already renders. */
  videoRef: React.RefObject<HTMLVideoElement>;
  /** Public URL of the commentary WAV (typically /api/commentary/[id]/track). */
  trackUrl: string | null;
  /** Time-aligned narration segments. */
  segments: CommentSegment[];
};

export type ActiveSegment = {
  segment: CommentSegment;
  /** 0..1 — how far through the narration we are. */
  progress: number;
};

export type ActiveVisualization = {
  segment: CommentSegment;
  url: string;
  /** Seconds remaining before we return to source. */
  remaining: number;
};

export type CommentaryPlayerState = {
  isOn: boolean;
  toggle: () => void;
  /** Current source-video time in seconds (mirrors video.currentTime). */
  currentTime: number;
  activeSegment: ActiveSegment | null;
  activeVisualization: ActiveVisualization | null;
  /** Effective source-video volume (1.0 normally, DUCK_VOLUME when commentary speaks). */
  sourceVolume: number;
};

export function useCommentaryPlayer({
  videoRef,
  trackUrl,
  segments,
}: UseCommentaryPlayerArgs): CommentaryPlayerState {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [isOn, setIsOn] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);

  // Lazily create the audio element so SSR doesn't choke and we don't
  // request the WAV until commentary is first turned on.
  useEffect(() => {
    if (!isOn || !trackUrl) return;
    if (!audioRef.current) {
      const a = new Audio(trackUrl);
      a.preload = "auto";
      audioRef.current = a;
    }
    return () => {
      // Keep the element across toggles to avoid re-downloading; only
      // clean up on unmount via the second effect below.
    };
  }, [isOn, trackUrl]);

  // Final cleanup on unmount.
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = "";
        audioRef.current = null;
      }
    };
  }, []);

  // Mirror video.currentTime → state, and keep audio playhead aligned.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const handleTime = () => {
      setCurrentTime(video.currentTime);
      const audio = audioRef.current;
      if (!audio || !isOn) return;
      // Resync the commentary track if the player drifts (scrub, seek,
      // pause/resume). 0.25s tolerance keeps us from thrashing.
      if (Math.abs(audio.currentTime - video.currentTime) > 0.25) {
        audio.currentTime = video.currentTime;
      }
    };
    const handlePlay = () => {
      const audio = audioRef.current;
      if (audio && isOn) {
        audio.currentTime = video.currentTime;
        void audio.play().catch(() => {
          // Autoplay can be blocked even after toggle — fail silently;
          // the toggle click that set isOn already constituted user intent.
        });
      }
    };
    const handlePause = () => {
      audioRef.current?.pause();
    };
    const handleSeeking = () => {
      const audio = audioRef.current;
      if (audio) audio.currentTime = video.currentTime;
    };
    video.addEventListener("timeupdate", handleTime);
    video.addEventListener("play", handlePlay);
    video.addEventListener("pause", handlePause);
    video.addEventListener("seeking", handleSeeking);
    return () => {
      video.removeEventListener("timeupdate", handleTime);
      video.removeEventListener("play", handlePlay);
      video.removeEventListener("pause", handlePause);
      video.removeEventListener("seeking", handleSeeking);
    };
  }, [videoRef, isOn]);

  // React to the toggle: start or stop the commentary audio in lockstep
  // with the video's current play state.
  useEffect(() => {
    const video = videoRef.current;
    const audio = audioRef.current;
    if (!audio) return;
    if (isOn && video) {
      audio.currentTime = video.currentTime;
      if (!video.paused) {
        void audio.play().catch(() => {});
      }
    } else {
      audio.pause();
    }
  }, [isOn, videoRef]);

  const toggle = useCallback(() => {
    setIsOn((v) => !v);
  }, []);

  const activeSegment = useMemo<ActiveSegment | null>(() => {
    if (!isOn) return null;
    for (const s of segments) {
      const start = s.start_seconds;
      const end = start + Math.max(0.1, s.duration_sec || 0);
      if (currentTime >= start && currentTime < end) {
        const progress = Math.min(1, Math.max(0, (currentTime - start) / (end - start)));
        return { segment: s, progress };
      }
    }
    return null;
  }, [isOn, segments, currentTime]);

  const activeVisualization = useMemo<ActiveVisualization | null>(() => {
    if (!isOn) return null;
    for (const s of segments) {
      if (!s.visualization_url) continue;
      const start = s.start_seconds;
      const dur = s.visualization_duration_sec || DEFAULT_VIS_DURATION_SEC;
      const end = start + dur;
      if (currentTime >= start && currentTime < end) {
        return { segment: s, url: s.visualization_url, remaining: end - currentTime };
      }
    }
    return null;
  }, [isOn, segments, currentTime]);

  // Apply duck volume to the source video while commentary speaks.
  // We mutate video.volume directly so the page's existing controls still
  // see the canonical state via the element.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = isOn && activeSegment ? DUCK_VOLUME : 1.0;
  }, [videoRef, isOn, activeSegment]);

  const sourceVolume = isOn && activeSegment ? DUCK_VOLUME : 1.0;

  return { isOn, toggle, currentTime, activeSegment, activeVisualization, sourceVolume };
}
