// GET /api/hero/[reportId]                       → video/mp4 stream of hero.mp4
// GET /api/hero/[reportId]?format=manifest        → JSON manifest
// GET /api/hero/[reportId]?format=manifest.json   → JSON manifest (alias)
//
// Reads the artefacts produced by ai_editor.hero_composer:
//   <HERO_DIR>/<reportId>/hero.mp4
//   <HERO_DIR>/<reportId>/manifest.json
// where HERO_DIR defaults to ./output/hero. The Stage-6 (India-v3) hero
// composer always writes per-id, so no flat fallback like the commentary
// route — but we still try both because reading manifests is cheap.
//
// Drop-in usage: copy this directory tree under
//   web/learndocumentary/src/app/api/hero/[reportId]/route.ts
// in the StudioOS-v1 repo. The defaults assume cwd is web/learndocumentary
// and hero output has been synced to public/hero/<id>/ — see
// docs/india-v3-handoff.md for the deploy path. Env override: HERO_DIR=/abs/path.

import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

export const dynamic = "force-dynamic";

type RouteContext = { params: { reportId: string } };

const SAFE_ID = /^[A-Za-z0-9._-]+$/;

function resolveHeroDir(reportId: string): string | null {
  const baseEnv = process.env.HERO_DIR;
  const candidates: string[] = [];
  if (baseEnv) candidates.push(path.join(baseEnv, reportId), baseEnv);
  candidates.push(
    path.join(process.cwd(), "output", "hero", reportId),
    path.join(process.cwd(), "output", "hero"),
    path.join(process.cwd(), "..", "..", "output", "hero", reportId),
    path.join(process.cwd(), "..", "..", "output", "hero"),
    path.join(process.cwd(), "public", "hero", reportId),
    path.join(process.cwd(), "public", "hero"),
  );
  for (const c of candidates) {
    try {
      if (
        fs.existsSync(path.join(c, "hero.mp4")) &&
        fs.existsSync(path.join(c, "manifest.json"))
      ) {
        return c;
      }
    } catch {
      // try next
    }
  }
  return null;
}

export async function GET(req: NextRequest, ctx: RouteContext) {
  const { reportId } = ctx.params;
  if (!SAFE_ID.test(reportId)) {
    return NextResponse.json({ error: "invalid reportId" }, { status: 400 });
  }
  const dir = resolveHeroDir(reportId);
  if (!dir) {
    return NextResponse.json(
      { error: `no hero artefacts found for "${reportId}"` },
      { status: 404 },
    );
  }
  const url = new URL(req.url);
  const format = url.searchParams.get("format");
  if (format === "manifest" || format === "manifest.json") {
    return manifestJson(reportId, dir);
  }
  return streamMp4(path.join(dir, "hero.mp4"), req);
}

function manifestJson(reportId: string, dir: string): NextResponse {
  let raw: any;
  try {
    raw = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf-8"));
  } catch (e: any) {
    return NextResponse.json(
      { error: "failed to read manifest.json", detail: String(e?.message || e) },
      { status: 500 },
    );
  }
  // Strip server-local paths from the response — clients should fetch
  // through this route, not the absolute disk path.
  const sanitized = {
    report_id: raw.report_id ?? reportId,
    duration_sec: raw.duration_sec ?? null,
    duration_minutes: raw.duration_minutes ?? null,
    character_id: raw.character_id ?? null,
    spent_estimated_credits: raw.spent_estimated_credits ?? null,
    hero_url: `/api/hero/${encodeURIComponent(reportId)}`,
    segments: Array.isArray(raw.segments)
      ? raw.segments.map((s: any) => ({
          id: s.id,
          kind: s.kind,
          type_label: s.type_label,
          impact: s.impact,
          title: s.title,
          source_in_seconds: s.source_in_seconds,
          source_out_seconds: s.source_out_seconds,
          curated_in_seconds: s.curated_in_seconds,
          curated_out_seconds: s.curated_out_seconds,
          duration_sec: s.duration_sec,
          narration_text: s.narration_text,
          aleph_prompt: s.aleph_prompt,
          vfx_category: s.vfx_category,
          aleph_offset_in_segment: s.aleph_offset_in_segment ?? null,
          // expose presence flags rather than disk paths
          has_avatar: !!s.avatar_path,
          has_aleph: !!s.aleph_path,
          has_tts: !!s.tts_path,
        }))
      : [],
    tracks: raw.tracks ?? null,
  };
  return NextResponse.json(sanitized, {
    headers: { "Cache-Control": "no-store" },
  });
}

function streamMp4(filePath: string, req: NextRequest): NextResponse {
  let stat: fs.Stats;
  try {
    stat = fs.statSync(filePath);
  } catch (e: any) {
    return NextResponse.json(
      { error: "failed to stat hero.mp4", detail: String(e?.message || e) },
      { status: 500 },
    );
  }
  const total = stat.size;
  const range = req.headers.get("range");

  // 206 Partial Content for native <video> seeking.
  if (range) {
    const m = /bytes=(\d+)-(\d+)?/.exec(range);
    if (m) {
      const start = parseInt(m[1], 10);
      const end = m[2] ? parseInt(m[2], 10) : Math.min(start + 1024 * 1024 * 4, total - 1);
      const chunkSize = end - start + 1;
      const fd = fs.openSync(filePath, "r");
      const buf = Buffer.alloc(chunkSize);
      fs.readSync(fd, buf, 0, chunkSize, start);
      fs.closeSync(fd);
      return new NextResponse(buf, {
        status: 206,
        headers: {
          "Content-Type": "video/mp4",
          "Content-Range": `bytes ${start}-${end}/${total}`,
          "Accept-Ranges": "bytes",
          "Content-Length": String(chunkSize),
          "Cache-Control": "public, max-age=3600",
        },
      });
    }
  }
  const buf = fs.readFileSync(filePath);
  return new NextResponse(buf, {
    headers: {
      "Content-Type": "video/mp4",
      "Content-Length": String(buf.byteLength),
      "Accept-Ranges": "bytes",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
