"use client";

// Corner overlay for the AI Jonny avatar. Saturday stretch — until the
// Runway Characters/avatar_videos pipeline lands, we render a styled
// placeholder so the layout / positioning / show-on-speak behavior is
// already wired and the avatar plug-in is one prop change.
//
// When `avatarVideoUrl` is supplied, we render that video looping in the
// corner. When omitted, we render a monogram placeholder. Either way, the
// overlay only appears while commentary is on; it gently emphasises while
// a narration segment is actively speaking.
//
// Editorial-commitment compliance: this avatar is the AI editor's mascot
// in the player UI only — it never replaces real subjects in the cut.

import React from "react";
import type { ActiveSegment } from "./useCommentaryPlayer";

export type AvatarOverlayProps = {
  /** Player-on flag from useCommentaryPlayer. */
  isOn: boolean;
  /** Currently-speaking segment, if any. Drives the talking emphasis. */
  activeSegment: ActiveSegment | null;
  /**
   * Optional avatar video URL (a short looped clip from Runway
   * avatar_videos). When present we render it; when absent we show the
   * monogram placeholder.
   */
  avatarVideoUrl?: string | null;
  /** Optional avatar still image URL — used as the placeholder backdrop. */
  avatarImageUrl?: string | null;
  /** Where to dock the overlay in the player frame. Defaults to bottom-right. */
  corner?: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  /** Avatar size in pixels. Default 96. */
  size?: number;
};

export function AvatarOverlay({
  isOn,
  activeSegment,
  avatarVideoUrl,
  avatarImageUrl,
  corner = "bottom-right",
  size = 96,
}: AvatarOverlayProps): React.ReactElement | null {
  if (!isOn) return null;
  const speaking = activeSegment !== null;

  const offset = 18;
  const cornerStyle: React.CSSProperties = (() => {
    switch (corner) {
      case "top-left": return { top: offset, left: offset };
      case "top-right": return { top: offset, right: offset };
      case "bottom-left": return { bottom: offset, left: offset };
      case "bottom-right": default: return { bottom: offset, right: offset };
    }
  })();

  return (
    <div
      aria-label="AI editor"
      style={{
        position: "absolute",
        ...cornerStyle,
        width: size,
        height: size,
        zIndex: 20,
        borderRadius: "50%",
        overflow: "hidden",
        background: "#1a1a1a",
        boxShadow: speaking
          ? `0 0 0 3px #ff66cc, 0 8px 28px rgba(255,102,204,0.45)`
          : `0 0 0 1px rgba(255,255,255,0.18), 0 6px 20px rgba(0,0,0,0.45)`,
        transition: "box-shadow 200ms ease-out, transform 200ms ease-out",
        transform: speaking ? "scale(1.04)" : "scale(1)",
        pointerEvents: "none",
      }}
    >
      {avatarVideoUrl ? (
        <video
          src={avatarVideoUrl}
          autoPlay
          loop
          muted
          playsInline
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      ) : avatarImageUrl ? (
        <img
          src={avatarImageUrl}
          alt=""
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      ) : (
        <PlaceholderMonogram speaking={speaking} />
      )}

      {/* Talking indicator — small dot at the base, only while a segment plays. */}
      {speaking && (
        <span
          aria-hidden
          style={{
            position: "absolute",
            right: 6,
            bottom: 6,
            width: 12,
            height: 12,
            borderRadius: "50%",
            background: "#ff66cc",
            boxShadow: "0 0 0 2px #1a1a1a",
            animation: "avatar-pulse 1s ease-in-out infinite",
          }}
        />
      )}

      <style jsx>{`
        @keyframes avatar-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(0.7); opacity: 0.5; }
        }
      `}</style>
    </div>
  );
}

function PlaceholderMonogram({ speaking }: { speaking: boolean }): React.ReactElement {
  // "JvW" is the founder monogram; this is the neutral mascot stand-in
  // until the Runway Characters API result is wired in.
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background:
          "radial-gradient(ellipse at 35% 30%, rgba(255,102,204,0.35) 0%, rgba(26,26,26,1) 70%)",
        color: "#fff",
        fontFamily: "'Inter', sans-serif",
        fontSize: "1.05rem",
        fontWeight: 600,
        letterSpacing: "0.05em",
        textShadow: speaking ? "0 0 14px rgba(255,102,204,0.6)" : "none",
      }}
    >
      JvW
    </div>
  );
}
