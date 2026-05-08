"use client";

// Audio-commentary toggle for the analysis-preview player. When ON, the
// commentary WAV plays time-aligned to the source video and the source
// audio is ducked to 0.5 while a narration segment is speaking. When OFF,
// the source plays normally.
//
// All sync state lives in useCommentaryPlayer — this component is the
// button + the on-screen "now speaking" beat indicator. Drop it into any
// page that already renders a <video ref={videoRef}>; the page passes the
// same ref + segments/track into the hook and into the sibling
// InlineVisualization / AvatarOverlay components.
//
// Reference (read-only): R:\--CODE--\StudioOS-v1\web\learndocumentary\src\app\analysis-preview
//   — extends that surface with a commentary track without modifying its
//   existing components.

import React from "react";
import type { CommentaryPlayerState } from "./useCommentaryPlayer";

export type CommentaryToggleProps = {
  player: CommentaryPlayerState;
  /** Disable the toggle (e.g. while the manifest is still loading). */
  disabled?: boolean;
  /** Override the label; defaults to "AI commentary". */
  label?: string;
  /** Optional className for outer container styling. */
  className?: string;
};

export function CommentaryToggle({
  player,
  disabled,
  label = "AI commentary",
  className,
}: CommentaryToggleProps): React.ReactElement {
  const { isOn, toggle, activeSegment } = player;
  const speaking = isOn && activeSegment !== null;

  return (
    <div className={className} style={{ display: "inline-flex", alignItems: "center", gap: "0.75rem" }}>
      <button
        type="button"
        onClick={toggle}
        disabled={disabled}
        aria-pressed={isOn}
        aria-label={isOn ? `Turn off ${label}` : `Turn on ${label}`}
        style={{
          fontFamily: "'Inter', sans-serif",
          fontSize: "11px",
          fontWeight: 700,
          letterSpacing: "0.15em",
          textTransform: "uppercase",
          padding: "9px 18px",
          border: isOn ? "1px solid #ff66cc" : "1px solid rgba(255,255,255,0.2)",
          background: isOn ? "#ff66cc" : "transparent",
          color: isOn ? "#fff" : "rgba(255,255,255,0.8)",
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.4 : 1,
          transition: "background 0.15s ease, color 0.15s ease, border-color 0.15s ease",
          display: "inline-flex",
          alignItems: "center",
          gap: "0.5rem",
          boxShadow: isOn ? "0 6px 20px rgba(255,102,204,0.35)" : "none",
        }}
      >
        <SpeakerIcon active={isOn} />
        <span>{label}</span>
        {isOn && (
          <span
            aria-hidden
            style={{
              display: "inline-block",
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: speaking ? "#fff" : "rgba(255,255,255,0.5)",
              animation: speaking ? "pulse-dot 1s ease-in-out infinite" : "none",
            }}
          />
        )}
      </button>

      {isOn && activeSegment && (
        <span
          aria-live="polite"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: "10px",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "rgba(255,255,255,0.45)",
            maxWidth: 320,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
          title={activeSegment.segment.narration_text}
        >
          {activeSegment.segment.timecode} &middot; {activeSegment.segment.type.toLowerCase()}
        </span>
      )}

      <style jsx>{`
        @keyframes pulse-dot {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.7); }
        }
      `}</style>
    </div>
  );
}

function SpeakerIcon({ active }: { active: boolean }): React.ReactElement {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" />
      {active && <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />}
      {active && <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />}
    </svg>
  );
}
