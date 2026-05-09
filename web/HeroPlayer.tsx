"use client";

// HeroPlayer — the cinematic top-of-page video for the analysis-preview
// surface. India-v3 (Sunday): the AI editor's curated 5-7 min hero cut
// is the FIRST thing judges see when they land on
// /analysis-preview/[id]. The existing analytical components
// (EmotionalCurve, MarkedUpTranscript, NoteItem, StoryAxis) scroll below
// and stay unchanged.
//
// Design contract:
//   - 16:9, full-width, max-width=1280
//   - Auto-mute on initial render (browser autoplay compliance)
//   - Click-to-unmute toggle in the corner
//   - Native scrub controls fine — no custom controller for now
//   - Below the player: a "Try with your own video →" CTA that links
//     to the existing upload flow.
//
// Source MP4 resolution:
//   1. If `videoUrl` prop is given, use it directly.
//   2. Otherwise hit `/api/hero/<reportId>` which streams from
//      <repo-root>/output/hero/<reportId>/hero.mp4 (or the static copy
//      at /hero/<reportId>/hero.mp4 if the API route isn't mounted).
//   3. Final fallback: `/hero/<reportId>/hero.mp4` (static).

import React, { useEffect, useRef, useState } from "react";

export type HeroPlayerProps = {
  reportId: string;
  /** Direct override URL — skips manifest + API probe. */
  videoUrl?: string | null;
  /** Optional poster shown before play. */
  posterUrl?: string | null;
  /** Optional manifest URL — overrides default `/api/hero/<id>/manifest.json`. */
  manifestUrl?: string | null;
  /** CTA target. Defaults to the upload flow at `/analyze`. */
  ctaHref?: string;
  /** CTA label override. */
  ctaLabel?: string;
};

type HeroManifest = {
  hero_path?: string;
  hero_url?: string | null;
  duration_sec?: number;
  duration_minutes?: number;
  segments?: Array<{
    id: string;
    title: string;
    type_label: string;
    impact: string;
    curated_in_seconds: number;
    curated_out_seconds: number;
    narration_text: string;
    kind: string;
  }>;
};

export default function HeroPlayer({
  reportId,
  videoUrl,
  posterUrl,
  manifestUrl,
  ctaHref = "/analyze",
  ctaLabel = "Try with your own video",
}: HeroPlayerProps): React.ReactElement {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState<boolean>(true);
  const [manifest, setManifest] = useState<HeroManifest | null>(null);
  const [resolvedVideoUrl, setResolvedVideoUrl] = useState<string | null>(
    videoUrl ?? null,
  );

  // Resolve the manifest + video URL.
  useEffect(() => {
    if (videoUrl) {
      setResolvedVideoUrl(videoUrl);
      return;
    }
    let cancelled = false;
    const fallbackVideo = `/hero/${encodeURIComponent(reportId)}/hero.mp4`;
    const url =
      manifestUrl ?? `/api/hero/${encodeURIComponent(reportId)}/manifest.json`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: HeroManifest | null) => {
        if (cancelled) return;
        setManifest(data);
        if (data?.hero_url) {
          setResolvedVideoUrl(data.hero_url);
        } else {
          // Default: API route serves hero.mp4 by id.
          setResolvedVideoUrl(`/api/hero/${encodeURIComponent(reportId)}`);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setResolvedVideoUrl(fallbackVideo);
      });
    return () => {
      cancelled = true;
    };
  }, [reportId, videoUrl, manifestUrl]);

  function toggleMute(): void {
    const v = videoRef.current;
    if (!v) return;
    const next = !v.muted;
    v.muted = next;
    setMuted(next);
    if (!next && v.paused) {
      // If we just unmuted while paused, kick playback so the user hears it.
      v.play().catch(() => undefined);
    }
  }

  return (
    <section
      className="ai-editor-hero"
      style={{
        background: "linear-gradient(180deg, #050505 0%, #0c0c0c 100%)",
        borderRadius: 6,
        padding: 24,
        margin: "0 auto 32px",
        maxWidth: 1280,
        color: "#fff",
        fontFamily: "'Inter', sans-serif",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 16,
          marginBottom: 14,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div
            style={{
              fontSize: 11,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "rgba(255,255,255,0.4)",
              marginBottom: 6,
            }}
          >
            AI editor · cinematic preview
          </div>
          <h2
            style={{
              fontSize: 22,
              fontWeight: 600,
              letterSpacing: "-0.01em",
              margin: 0,
            }}
          >
            What the AI editor noticed
          </h2>
          <div
            style={{
              fontSize: 13,
              color: "rgba(255,255,255,0.55)",
              marginTop: 4,
            }}
          >
            {manifest?.duration_minutes
              ? `${manifest.duration_minutes} min curated cut · ${
                  manifest.segments?.length ?? "·"
                } editorial beats`
              : "Curated cut — voice + likeness + Aleph"}
          </div>
        </div>
        <div
          style={{
            fontSize: 11,
            letterSpacing: "0.15em",
            textTransform: "uppercase",
            color: "rgba(255,255,255,0.35)",
          }}
        >
          made with runway · elevenlabs · anthropic
        </div>
      </div>

      <div
        style={{
          position: "relative",
          width: "100%",
          aspectRatio: "16 / 9",
          background: "#000",
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        {resolvedVideoUrl ? (
          <video
            ref={videoRef}
            src={resolvedVideoUrl}
            poster={posterUrl ?? undefined}
            autoPlay
            muted
            playsInline
            controls
            preload="metadata"
            style={{
              width: "100%",
              height: "100%",
              display: "block",
              background: "#000",
            }}
          />
        ) : (
          <div
            style={{
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "rgba(255,255,255,0.4)",
              fontSize: 12,
              letterSpacing: "0.15em",
              textTransform: "uppercase",
            }}
          >
            loading hero…
          </div>
        )}

        <button
          type="button"
          onClick={toggleMute}
          aria-label={muted ? "Unmute hero video" : "Mute hero video"}
          style={{
            position: "absolute",
            right: 16,
            top: 16,
            background: "rgba(0,0,0,0.7)",
            color: "#fff",
            border: "1px solid rgba(255,255,255,0.25)",
            borderRadius: 999,
            padding: "8px 14px",
            fontSize: 11,
            letterSpacing: "0.15em",
            textTransform: "uppercase",
            cursor: "pointer",
            backdropFilter: "blur(8px)",
          }}
        >
          {muted ? "🔇 click to hear it" : "🔊 sound on"}
        </button>
      </div>

      <div
        style={{
          marginTop: 18,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div
          style={{
            fontSize: 12,
            color: "rgba(255,255,255,0.5)",
            maxWidth: 640,
            lineHeight: 1.5,
          }}
        >
          The AI editor reads the cut, narrates its notes in my cloned voice,
          appears as the avatar in the corner, and runs Aleph (gen4) over
          moments where atmosphere would land harder. The full analytical
          breakdown — hook scoring, story axis, marked transcript — is
          below.
        </div>
        <a
          href={ctaHref}
          style={{
            background: "#fff",
            color: "#000",
            textDecoration: "none",
            padding: "10px 18px",
            borderRadius: 999,
            fontSize: 12,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            fontWeight: 600,
            whiteSpace: "nowrap",
          }}
        >
          {ctaLabel} →
        </a>
      </div>
    </section>
  );
}
