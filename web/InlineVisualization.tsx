"use client";

// Inline visualization overlay. When the commentary hook reports an
// active segment with a `visualization_url`, this component covers the
// source video frame with that clip for ~3-5 sec, then steps aside and
// the source returns. The narration audio keeps playing over both.
//
// Stage-1 reality: visualization_url is undefined for every segment, so
// this renders nothing. Saturday's Echo-v3 work attaches Aleph-transformed
// clips to the segments JSON; this component then lights up automatically.
//
// Until Echo ships, the component supports a `mock` prop that injects a
// placeholder URL onto the first suggestion segment so the wiring can be
// demoed end-to-end without a real Aleph result.

import React, { useEffect, useRef } from "react";
import type { ActiveVisualization } from "./useCommentaryPlayer";

export type InlineVisualizationProps = {
  active: ActiveVisualization | null;
  /**
   * Bounding rect (relative to the page) of the source <video>. We mount
   * this overlay absolutely positioned so it covers exactly that region.
   * If unset, the overlay fills its parent — wrap it in a relatively-
   * positioned container around the source video.
   */
  videoRect?: { width: number; height: number } | null;
  /** Hide the "AI ENHANCED" badge if you want a cleaner look in demos. */
  hideBadge?: boolean;
};

export function InlineVisualization({
  active,
  videoRect,
  hideBadge,
}: InlineVisualizationProps): React.ReactElement | null {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // Restart playback when the active segment changes so each Aleph clip
  // begins from frame 0.
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !active) return;
    v.currentTime = 0;
    void v.play().catch(() => {});
  }, [active?.url]);

  if (!active) return null;

  return (
    <div
      aria-label="AI-enhanced visualization"
      style={{
        position: "absolute",
        inset: 0,
        width: videoRect?.width ?? "100%",
        height: videoRect?.height ?? "100%",
        background: "#000",
        zIndex: 10,
        overflow: "hidden",
        animation: "vis-fade-in 220ms ease-out",
      }}
    >
      <video
        ref={videoRef}
        src={active.url}
        muted
        playsInline
        autoPlay
        // Stage-2 Aleph clips are short (3-5 sec); we don't loop because the
        // hook will dismiss us when remaining hits 0.
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />

      {!hideBadge && (
        <div
          style={{
            position: "absolute",
            top: 14,
            left: 14,
            display: "inline-flex",
            alignItems: "center",
            gap: "0.5rem",
            padding: "6px 10px",
            background: "rgba(255,102,204,0.95)",
            color: "#fff",
            fontFamily: "'Inter', sans-serif",
            fontSize: "9px",
            fontWeight: 700,
            letterSpacing: "0.2em",
            textTransform: "uppercase",
            borderRadius: 2,
          }}
        >
          <span
            aria-hidden
            style={{
              display: "inline-block",
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "#fff",
              animation: "vis-blink 0.9s ease-in-out infinite",
            }}
          />
          AI Enhanced
        </div>
      )}

      {active.segment.narration_text && (
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 0,
            padding: "16px 20px 18px",
            background:
              "linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0) 100%)",
            color: "#fff",
            fontFamily: "'Inter', sans-serif",
            fontSize: "0.95rem",
            lineHeight: 1.45,
            fontStyle: "italic",
            fontWeight: 300,
            textShadow: "0 1px 2px rgba(0,0,0,0.6)",
            pointerEvents: "none",
          }}
        >
          {active.segment.narration_text}
        </div>
      )}

      <style jsx>{`
        @keyframes vis-fade-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes vis-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
