// GET /api/commentary/[reportId]              → CommentaryManifest JSON
// GET /api/commentary/[reportId]?format=wav   → audio/wav stream of the
//                                               full-duration commentary track
//
// Reads the artefacts produced by ai_editor.commentary_synthesizer:
//   <COMMENTARY_DIR>/<reportId>/commentary_segments.json
//   <COMMENTARY_DIR>/<reportId>/commentary.wav
// where COMMENTARY_DIR defaults to ./output/commentary. Stage 1 only ran
// the synthesizer once into ./output/commentary directly, so we fall back
// to that path when no per-id subdir exists.
//
// Drop-in usage: copy this directory tree under
//   web/learndocumentary/src/app/api/commentary/[reportId]/route.ts
// in the StudioOS-v1 repo. The defaults assume cwd is web/learndocumentary
// (Vercel sets it via rootDirectory) and the synthesizer output has been
// synced to public/commentary/<id>/ — see web/README.md for the deploy
// path. Env override: COMMENTARY_DIR=/abs/path.

import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import type { CommentSegment, CommentaryManifest } from "../../../types";

export const dynamic = "force-dynamic";

type RouteContext = { params: { reportId: string } };

const SAFE_ID = /^[A-Za-z0-9._-]+$/;

function resolveCommentaryDir(reportId: string): string | null {
  // Allow CWD-relative or absolute via env. The web/learndocumentary deploy
  // sets rootDirectory to that subdir, so process.cwd() lands there; the
  // synthesizer writes into ../../../output/commentary by default.
  const baseEnv = process.env.COMMENTARY_DIR;
  const candidates: string[] = [];
  if (baseEnv) candidates.push(path.join(baseEnv, reportId), baseEnv);
  candidates.push(
    path.join(process.cwd(), "output", "commentary", reportId),
    path.join(process.cwd(), "output", "commentary"),
    path.join(process.cwd(), "..", "..", "output", "commentary", reportId),
    path.join(process.cwd(), "..", "..", "output", "commentary"),
    path.join(process.cwd(), "public", "commentary", reportId),
    path.join(process.cwd(), "public", "commentary"),
  );
  for (const c of candidates) {
    try {
      if (
        fs.existsSync(path.join(c, "commentary_segments.json")) &&
        fs.existsSync(path.join(c, "commentary.wav"))
      ) {
        return c;
      }
    } catch {
      // ignore stat errors and try the next candidate
    }
  }
  return null;
}

export async function GET(req: NextRequest, ctx: RouteContext) {
  const { reportId } = ctx.params;
  if (!SAFE_ID.test(reportId)) {
    return NextResponse.json({ error: "invalid reportId" }, { status: 400 });
  }

  const dir = resolveCommentaryDir(reportId);
  if (!dir) {
    return NextResponse.json(
      { error: `no commentary artefacts found for "${reportId}"` },
      { status: 404 },
    );
  }

  const url = new URL(req.url);
  const format = url.searchParams.get("format");
  if (format === "wav") {
    return streamWav(path.join(dir, "commentary.wav"));
  }

  return manifestJson(reportId, dir, req);
}

function manifestJson(reportId: string, dir: string, req: NextRequest): NextResponse {
  let raw: any;
  try {
    raw = JSON.parse(fs.readFileSync(path.join(dir, "commentary_segments.json"), "utf-8"));
  } catch (e: any) {
    return NextResponse.json(
      { error: "failed to read commentary_segments.json", detail: String(e?.message || e) },
      { status: 500 },
    );
  }

  const segments: CommentSegment[] = Array.isArray(raw?.segments)
    ? raw.segments.map((s: any) => ({
        comment_id: String(s.comment_id || ""),
        type: String(s.type || ""),
        timecode: String(s.timecode || ""),
        start_seconds: Number(s.start_seconds || 0),
        end_seconds: Number(s.end_seconds || s.start_seconds || 0),
        impact: String(s.impact || ""),
        narration_text: String(s.narration_text || ""),
        // Server-local paths are debug-only; never leak to the browser.
        audio_path: null,
        duration_sec: Number(s.duration_sec || 0),
        is_suggestion: Boolean(s.is_suggestion ?? true),
        cached: Boolean(s.cached ?? false),
        visualization_url: s.visualization_url || null,
        visualization_duration_sec: s.visualization_duration_sec
          ? Number(s.visualization_duration_sec)
          : undefined,
      }))
    : [];

  // The track URL is the same route with ?format=wav so the player
  // doesn't need to know about a separate static path. Cached aggressively
  // because the WAV is content-addressed by reportId.
  const trackUrl = new URL(req.url);
  trackUrl.search = "format=wav";

  const manifest: CommentaryManifest = {
    report_path: String(raw?.report_path || ""),
    track_url: trackUrl.toString(),
    total_duration_sec: Number(raw?.total_duration_sec || 0),
    voice_preset: String(raw?.voice_preset || ""),
    model: String(raw?.model || ""),
    segments,
  };

  return NextResponse.json(manifest, {
    headers: {
      // Manifest is cheap; revalidate on each request so a fresh sync
      // shows up immediately.
      "Cache-Control": "no-store",
    },
  });
}

function streamWav(wavPath: string): NextResponse {
  let buf: Buffer;
  try {
    buf = fs.readFileSync(wavPath);
  } catch (e: any) {
    return NextResponse.json(
      { error: "failed to read commentary.wav", detail: String(e?.message || e) },
      { status: 500 },
    );
  }
  // Buffer is fine for Stage-1 sized tracks (Canyons commentary.wav is
  // ~100 MB silent + overlays). If we move to longer cuts, swap for a
  // ReadableStream + 206 Partial Content range support.
  return new NextResponse(buf, {
    headers: {
      "Content-Type": "audio/wav",
      "Content-Length": String(buf.byteLength),
      "Cache-Control": "public, max-age=3600",
      "Accept-Ranges": "none",
    },
  });
}
