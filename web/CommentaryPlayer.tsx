"use client";

// One-shot composition that wires CommentaryToggle + InlineVisualization
// + AvatarOverlay around a source <video>. Drop this above an existing
// player and pass the reportId; it fetches the manifest from
// /api/commentary/<reportId>, sets up the time-aligned audio track, and
// renders all three child components with shared state.
//
// If you want different layout — toggle in the page header, overlays in
// the player frame — call useCommentaryPlayer directly and lay out the
// components yourself. This composition is the easy path for the
// hackathon analysis-preview integration.

import React, { useEffect, useRef, useState } from "react";
import { CommentaryToggle } from "./CommentaryToggle";
import { InlineVisualization } from "./InlineVisualization";
import { AvatarOverlay } from "./AvatarOverlay";
import { useCommentaryPlayer } from "./useCommentaryPlayer";
import type { CommentaryManifest } from "./types";

export type CommentaryPlayerProps = {
  reportId: string;
  /** Source video URL — the documentary cut. */
  videoSrc: string;
  /** Override the default manifest URL pattern. */
  manifestUrl?: string;
  /** Optional poster for the source video. */
  poster?: string;
  /** Display height for the video frame. */
  height?: number | string;
  /** Optional Runway avatar_videos URL. */
  avatarVideoUrl?: string | null;
  /** Optional avatar still. */
  avatarImageUrl?: string | null;
  /** When true, force a placeholder visualization on the first suggestion. Used for demo before Echo-v3 ships. */
  mockVisualization?: boolean;
  /** Mock URL to use when mockVisualization is true. */
  mockVisualizationUrl?: string;
};

export function CommentaryPlayer({
  reportId,
  videoSrc,
  manifestUrl,
  poster,
  height = 480,
  avatarVideoUrl,
  avatarImageUrl,
  mockVisualization,
  mockVisualizationUrl,
}: CommentaryPlayerProps): React.ReactElement {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [manifest, setManifest] = useState<CommentaryManifest | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const url = manifestUrl ?? `/api/commentary/${encodeURIComponent(reportId)}`;
    let cancelled = false;
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`manifest ${r.status}`);
        return r.json();
      })
      .then((data: CommentaryManifest) => {
        if (cancelled) return;
        if (mockVisualization && data.segments?.length) {
          // Inject a placeholder URL on the first suggestion so the
          // inline-vis seam can be demoed before Saturday's Aleph results
          // are wired in. Replace with `data` as-is once Echo ships.
          const url = mockVisualizationUrl ?? videoSrc;
          let injected = false;
          data = {
            ...data,
            segments: data.segments.map((s) => {
              if (!injected && s.is_suggestion) {
                injected = true;
                return { ...s, visualization_url: url, visualization_duration_sec: 4 };
              }
              return s;
            }),
          };
        }
        setManifest(data);
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [reportId, manifestUrl, mockVisualization, mockVisualizationUrl, videoSrc]);

  const player = useCommentaryPlayer({
    videoRef,
    trackUrl: manifest?.track_url ?? null,
    segments: manifest?.segments ?? [],
  });

  return (
    <div
      style={{
        position: "relative",
        background: "#0a0a0a",
        color: "#fff",
        borderRadius: 4,
        overflow: "hidden",
      }}
    >
      <div style={{ position: "relative", width: "100%", height }}>
        <video
          ref={videoRef}
          src={videoSrc}
          poster={poster}
          controls
          playsInline
          style={{ width: "100%", height: "100%", objectFit: "contain", display: "block", background: "#000" }}
        />
        <InlineVisualization active={player.activeVisualization} />
        <AvatarOverlay
          isOn={player.isOn}
          activeSegment={player.activeSegment}
          avatarVideoUrl={avatarVideoUrl}
          avatarImageUrl={avatarImageUrl}
        />
      </div>

      <div
        style={{
          padding: "14px 18px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "rgba(0,0,0,0.95)",
          borderTop: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        <CommentaryToggle player={player} disabled={!manifest} />
        <div
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: "10px",
            letterSpacing: "0.15em",
            textTransform: "uppercase",
            color: "rgba(255,255,255,0.3)",
          }}
        >
          {error
            ? `commentary unavailable: ${error}`
            : manifest
            ? `${manifest.segments.length} editorial beats · voice: ${manifest.voice_preset}`
            : "loading commentary…"}
        </div>
      </div>
    </div>
  );
}
