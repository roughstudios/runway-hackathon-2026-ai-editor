// Shared types for the analysis-preview commentary player extension.
// Source of truth for the CommentSegment shape produced by
// ai_editor.commentary_synthesizer.build_commentary().
//
// The runtime shape lives at output/commentary/commentary_segments.json
// (see ai_editor/commentary_synthesizer.py CommentSegment dataclass).
// Keep these in sync.

export type CommentSegment = {
  comment_id: string;
  type: string;
  timecode: string;
  start_seconds: number;
  end_seconds: number;
  impact: string;
  narration_text: string;
  /** Server-local path to the per-segment TTS clip (debug / cache provenance). */
  audio_path: string | null;
  /** TTS clip duration in seconds — how long the narration runs from start_seconds. */
  duration_sec: number;
  is_suggestion: boolean;
  cached: boolean;
  /**
   * Saturday: when Echo-v3 produces an Aleph-transformed clip for a suggestion,
   * the URL lands here and InlineVisualization plays it briefly inline at
   * start_seconds. Until then, undefined → no inline visualization.
   */
  visualization_url?: string | null;
  /** Optional inline visualization length override (default 4 sec). */
  visualization_duration_sec?: number;
};

export type CommentaryManifest = {
  report_path: string;
  /** Public URL the player loads — e.g. /api/commentary/<id>/track */
  track_url: string;
  total_duration_sec: number;
  voice_preset: string;
  model: string;
  segments: CommentSegment[];
};
