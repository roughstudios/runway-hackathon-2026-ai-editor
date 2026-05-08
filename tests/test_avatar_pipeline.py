"""Smoke tests for the Stage 3 avatar pipeline.

The dry-run path requires no network and burns no credits — that's the
default gate. The live path is exercised manually with --limit and a
populated commentary_segments.json (post Charlie-v3 live TTS run).

Tests use mock objects rather than real Runway calls. The mocks mirror
the SDK's relevant return shapes precisely enough that the production
code paths execute end-to-end against them.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Make the repo root importable when this file is run directly. pytest
# in the StudioOS .venv hangs (per Charlie-v3 / Echo-v3 handoffs); these
# tests are designed to be invoked directly via ``python -c "..."`` too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_editor.avatar_animator import (  # noqa: E402
    AVATAR_VIDEO_MODEL,
    AvatarAnimator,
    _audio_mime_for,
    _fingerprint,
    _report_id_from_payload,
    build_avatar_track,
)
from ai_editor.avatar_creator import (  # noqa: E402
    AVATAR_NAME,
    AVATAR_PERSONALITY,
    DEFAULT_VOICE_PRESET,
    JonnyCharacter,
    _record_from_response,
    get_or_create_jonny_character,
    list_reference_candidates,
)


# --- Mocks ---------------------------------------------------------------


@dataclass
class _FakeUpload:
    uri: str


@dataclass
class _FakeAvatarReady:
    id: str
    name: str
    status: str
    reference_image_uri: Optional[str] = None
    created_at: Optional[str] = None
    failure_reason: Optional[str] = None


class _FakeUploadsResource:
    def __init__(self, log: List[tuple]):
        self._log = log
        self._counter = 0

    def create_ephemeral(self, *, file):
        self._counter += 1
        filename = file[0]
        self._log.append(("upload", filename))
        return _FakeUpload(uri=f"runway://uploads/fake-{self._counter}-{filename}")


class _FakeAvatarsResource:
    def __init__(
        self,
        log: List[tuple],
        create_status: str = "READY",
        retrieve_status: str = "READY",
        avatar_id: str = "avatar-test-1",
    ):
        self._log = log
        self._create_status = create_status
        self._retrieve_status = retrieve_status
        self._id = avatar_id

    def create(self, *, name, personality, reference_image, voice, **kwargs):
        self._log.append(("avatars.create", name, voice["preset_id"]))
        return _FakeAvatarReady(
            id=self._id,
            name=name,
            status=self._create_status,
            reference_image_uri=reference_image,
            created_at="2026-05-08T19:30:00Z",
        )

    def retrieve(self, avatar_id, **kwargs):
        self._log.append(("avatars.retrieve", avatar_id))
        return _FakeAvatarReady(
            id=avatar_id,
            name=AVATAR_NAME,
            status=self._retrieve_status,
            reference_image_uri="runway://uploads/fake-1-jonny.jpg",
            created_at="2026-05-08T19:30:00Z",
        )


class _FakeTaskResult:
    def __init__(self, output_url: str):
        self.output = [output_url]


class _FakeTask:
    def __init__(self, task_id: str, output_url: str):
        self.id = task_id
        self._output_url = output_url

    def wait_for_task_output(self, timeout=None):
        return _FakeTaskResult(self._output_url)


class _FakeAvatarVideosResource:
    def __init__(self, log: List[tuple]):
        self._log = log
        self._counter = 0

    def create(self, *, avatar, model, speech, **kwargs):
        self._counter += 1
        self._log.append(
            ("avatar_videos.create", avatar["avatarId"], model, speech["type"])
        )
        return _FakeTask(
            task_id=f"task-{self._counter}",
            output_url=f"https://example.com/fake-video-{self._counter}.mp4",
        )


class _FakeRunway:
    def __init__(
        self,
        create_status: str = "READY",
        retrieve_status: str = "READY",
    ):
        self.log: List[tuple] = []
        self.uploads = _FakeUploadsResource(self.log)
        self.avatars = _FakeAvatarsResource(
            self.log, create_status=create_status, retrieve_status=retrieve_status,
        )
        self.avatar_videos = _FakeAvatarVideosResource(self.log)


# --- Tests ---------------------------------------------------------------


def test_audio_mime_mapping():
    assert _audio_mime_for(Path("a.mp3")) == "audio/mpeg"
    assert _audio_mime_for(Path("a.wav")) == "audio/wav"
    assert _audio_mime_for(Path("a.m4a")) == "audio/mp4"
    assert _audio_mime_for(Path("a.weird")) == "application/octet-stream"
    print("OK test_audio_mime_mapping")


def test_fingerprint_is_stable_and_content_keyed(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"audio-v1")
    b.write_bytes(b"audio-v1")  # same content
    fp_a = _fingerprint(audio_path=a, character_id="x", model=AVATAR_VIDEO_MODEL)
    fp_b = _fingerprint(audio_path=b, character_id="x", model=AVATAR_VIDEO_MODEL)
    assert fp_a == fp_b, "same audio content should fingerprint identically"
    b.write_bytes(b"audio-v2")
    fp_b2 = _fingerprint(audio_path=b, character_id="x", model=AVATAR_VIDEO_MODEL)
    assert fp_a != fp_b2, "different content should fingerprint differently"
    fp_other_char = _fingerprint(
        audio_path=a, character_id="y", model=AVATAR_VIDEO_MODEL,
    )
    assert fp_a != fp_other_char, "character id is part of the key"
    print("OK test_fingerprint_is_stable_and_content_keyed")


def test_record_from_response_normalizes_iso(tmp_path):
    from datetime import datetime, timezone
    resp = _FakeAvatarReady(
        id="avatar-x",
        name="Test",
        status="READY",
        reference_image_uri="runway://uploads/foo",
        created_at=datetime(2026, 5, 8, 12, 30, tzinfo=timezone.utc),
    )
    record = _record_from_response(
        resp, reference_image_path=str(tmp_path / "ref.jpg"), voice_preset="vincent",
    )
    assert record.id == "avatar-x"
    assert record.status == "READY"
    assert record.created_at == "2026-05-08T12:30:00+00:00"
    print("OK test_record_from_response_normalizes_iso")


def test_get_or_create_jonny_character_creates_when_no_cache(tmp_path):
    fake = _FakeRunway(create_status="READY")
    ref = tmp_path / "ref.jpg"
    ref.write_bytes(b"jpeg-bytes")
    cache = tmp_path / "cache.json"

    record = get_or_create_jonny_character(
        runway_client=fake,
        reference_image=ref,
        cache_path=cache,
    )

    assert record.status == "READY"
    assert record.cached is False
    assert cache.exists()
    saved = json.loads(cache.read_text())
    assert saved["id"] == record.id
    # Upload + create should have happened exactly once each.
    upload_count = sum(1 for e in fake.log if e[0] == "upload")
    create_count = sum(1 for e in fake.log if e[0] == "avatars.create")
    assert upload_count == 1
    assert create_count == 1
    print("OK test_get_or_create_jonny_character_creates_when_no_cache")


def test_get_or_create_jonny_character_uses_ready_cache(tmp_path):
    fake = _FakeRunway()
    ref = tmp_path / "ref.jpg"
    ref.write_bytes(b"jpeg-bytes")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({
        "id": "avatar-pre-existing",
        "name": "Pre",
        "status": "READY",
        "reference_image_path": str(ref),
        "reference_image_uri": "runway://uploads/pre",
        "voice_preset": "vincent",
        "created_at": "2026-05-08T01:00:00Z",
    }))

    record = get_or_create_jonny_character(
        runway_client=fake,
        reference_image=ref,
        cache_path=cache,
    )

    assert record.id == "avatar-pre-existing"
    assert record.cached is True
    # No API calls made.
    assert fake.log == []
    print("OK test_get_or_create_jonny_character_uses_ready_cache")


def test_get_or_create_jonny_character_polls_processing_cache(tmp_path):
    fake = _FakeRunway(retrieve_status="READY")
    ref = tmp_path / "ref.jpg"
    ref.write_bytes(b"jpeg-bytes")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({
        "id": "avatar-baking",
        "name": "Baking",
        "status": "PROCESSING",
        "reference_image_path": str(ref),
        "reference_image_uri": None,
        "voice_preset": "vincent",
        "created_at": None,
    }))

    record = get_or_create_jonny_character(
        runway_client=fake,
        reference_image=ref,
        cache_path=cache,
    )

    assert record.status == "READY"
    assert record.id == "avatar-baking"
    # Should have polled retrieve, but never re-created.
    assert any(e[0] == "avatars.retrieve" for e in fake.log)
    assert not any(e[0] == "avatars.create" for e in fake.log)
    print("OK test_get_or_create_jonny_character_polls_processing_cache")


def test_avatar_animator_caches_per_audio_content(tmp_path, monkeypatch):
    fake = _FakeRunway()
    cache_dir = tmp_path / "cache"
    audio = tmp_path / "seg.mp3"
    audio.write_bytes(b"fake-mp3-bytes")

    # Prevent _download from actually using httpx — pretend the download
    # succeeded and write a small placeholder.
    import ai_editor.avatar_animator as anim_mod

    def _fake_download(url, dest):
        dest.write_bytes(b"animated-mp4")

    monkeypatch.setattr(anim_mod, "_download", _fake_download)

    animator = AvatarAnimator(
        character_id="avatar-x",
        runway_client=fake,
        cache_dir=cache_dir,
    )

    first = animator.animate(audio, label="seg.0")
    assert first.cached is False
    assert first.video_path.exists()
    assert first.video_path.stat().st_size > 0

    second = animator.animate(audio, label="seg.0-rerun")
    assert second.cached is True
    assert second.video_path == first.video_path

    # Only one create call total; cache hit avoided the second.
    create_calls = [e for e in fake.log if e[0] == "avatar_videos.create"]
    assert len(create_calls) == 1
    upload_calls = [e for e in fake.log if e[0] == "upload"]
    assert len(upload_calls) == 1
    print("OK test_avatar_animator_caches_per_audio_content")


def test_build_avatar_track_dry_run(tmp_path):
    seg_audio = tmp_path / "seg0.mp3"
    seg_audio.write_bytes(b"")
    segments_json = tmp_path / "commentary_segments.json"
    segments_json.write_text(json.dumps({
        "report_path": str(tmp_path / "canyons_100_miles_1_3.json"),
        "track_path": str(tmp_path / "commentary.wav"),
        "total_duration_sec": 1114.9,
        "voice_preset": "vincent",
        "model": "eleven_multilingual_v2",
        "dry_run": True,
        "segments": [
            {
                "comment_id": "emotional.0",
                "type": "TENSION",
                "timecode": "00:00:31",
                "start_seconds": 31.67,
                "end_seconds": 34.0,
                "narration_text": "Tension here.",
                "audio_path": str(seg_audio),
                "duration_sec": 4.5,
                "is_suggestion": True,
            },
            {
                "comment_id": "emotional.1",
                "type": "VULNERABILITY",
                "timecode": "00:01:23",
                "start_seconds": 83.2,
                "end_seconds": 88.0,
                "narration_text": "Vulnerable beat.",
                "audio_path": str(seg_audio),
                "duration_sec": 6.0,
                "is_suggestion": True,
            },
        ],
    }))

    out_dir = tmp_path / "avatar_segments"
    manifest = build_avatar_track(
        segments_json_path=segments_json,
        out_dir=out_dir,
        dry_run=True,
    )

    assert manifest["dry_run"] is True
    assert manifest["report_id"] == "canyons_100_miles_1_3"
    assert manifest["stats"]["n_total"] == 2
    assert manifest["stats"]["n_animated"] == 2
    assert manifest["stats"]["runway_calls"] == 0
    # Per-segment placeholder MP4 files exist (zero-byte).
    for entry in manifest["segments"]:
        p = Path(entry["mp4_path"])
        assert p.exists()
        assert p.parent == out_dir
    # Manifest written.
    manifest_path = out_dir / "manifest.json"
    assert manifest_path.exists()
    parsed = json.loads(manifest_path.read_text())
    assert parsed["report_id"] == "canyons_100_miles_1_3"
    print("OK test_build_avatar_track_dry_run")


def test_build_avatar_track_live_path_with_mocks(tmp_path, monkeypatch):
    fake = _FakeRunway()
    seg_audio = tmp_path / "seg0.mp3"
    seg_audio.write_bytes(b"audio-bytes-for-fingerprint")
    segments_json = tmp_path / "segs.json"
    segments_json.write_text(json.dumps({
        "report_path": str(tmp_path / "report_xyz.json"),
        "total_duration_sec": 100.0,
        "segments": [
            {
                "comment_id": "emotional.0",
                "start_seconds": 10.0,
                "end_seconds": 14.0,
                "duration_sec": 4.0,
                "narration_text": "Test narration.",
                "audio_path": str(seg_audio),
                "is_suggestion": True,
            },
        ],
    }))

    import ai_editor.avatar_animator as anim_mod
    monkeypatch.setattr(
        anim_mod, "_download",
        lambda url, dest: dest.write_bytes(b"animated-mp4"),
    )

    out_dir = tmp_path / "out"
    cache_path = tmp_path / "char_cache.json"

    character = JonnyCharacter(
        id="avatar-test",
        name="Test",
        status="READY",
        reference_image_path=str(tmp_path / "ref.jpg"),
        reference_image_uri="runway://uploads/ref",
        voice_preset="vincent",
        created_at=None,
    )

    manifest = build_avatar_track(
        segments_json_path=segments_json,
        out_dir=out_dir,
        runway_client=fake,
        character=character,
        character_cache_path=cache_path,
        dry_run=False,
    )

    assert manifest["character_id"] == "avatar-test"
    assert manifest["stats"]["n_animated"] == 1
    assert manifest["stats"]["runway_calls"] == 1
    seg_entry = manifest["segments"][0]
    assert seg_entry["skipped_reason"] is None
    assert Path(seg_entry["mp4_path"]).exists()
    print("OK test_build_avatar_track_live_path_with_mocks")


def test_build_avatar_track_skips_missing_audio(tmp_path):
    segments_json = tmp_path / "segs.json"
    segments_json.write_text(json.dumps({
        "report_path": str(tmp_path / "rep.json"),
        "total_duration_sec": 10.0,
        "segments": [
            {
                "comment_id": "x.0",
                "start_seconds": 1.0,
                "end_seconds": 2.0,
                "duration_sec": 1.0,
                "narration_text": "n",
                "audio_path": None,
                "is_suggestion": False,
            },
        ],
    }))

    character = JonnyCharacter(
        id="a", name="A", status="READY",
        reference_image_path="/tmp/ref.jpg",
        reference_image_uri=None, voice_preset="vincent", created_at=None,
    )

    fake = _FakeRunway()
    out_dir = tmp_path / "out"
    manifest = build_avatar_track(
        segments_json_path=segments_json,
        out_dir=out_dir,
        runway_client=fake,
        character=character,
        dry_run=False,
    )
    assert manifest["stats"]["skipped"] == 1
    assert manifest["stats"]["runway_calls"] == 0
    assert manifest["segments"][0]["skipped_reason"]
    print("OK test_build_avatar_track_skips_missing_audio")


def test_report_id_from_payload_falls_back_when_no_report_path(tmp_path):
    p = tmp_path / "myrun" / "commentary_segments.json"
    p.parent.mkdir()
    assert _report_id_from_payload({}, p) == "myrun"
    flat = tmp_path / "commentary" / "commentary_segments.json"
    flat.parent.mkdir()
    assert _report_id_from_payload({}, flat) == "default"
    explicit = _report_id_from_payload(
        {"report_path": "/tmp/foo/canyons_100_miles_1_3.json"}, p,
    )
    assert explicit == "canyons_100_miles_1_3"
    print("OK test_report_id_from_payload_falls_back_when_no_report_path")


def test_list_reference_candidates_handles_missing_dir(tmp_path):
    assert list_reference_candidates(tmp_path / "no-such-dir") == []
    d = tmp_path / "frames"
    d.mkdir()
    (d / "a.jpg").write_bytes(b"a")
    (d / "b.png").write_bytes(b"b")
    (d / "c.txt").write_bytes(b"c")
    found = list_reference_candidates(d)
    assert {p.name for p in found} == {"a.jpg", "b.png"}
    print("OK test_list_reference_candidates_handles_missing_dir")


# --- Direct-invocation smoke runner --------------------------------------


def _run_all():
    """Invoke each test via tmp_path/monkeypatch shims so direct-mode runs.

    pytest in the StudioOS .venv hangs (per Charlie/Echo handoffs); this
    lets us run the suite without it.
    """
    import shutil, tempfile

    class _MonkeyPatch:
        def __init__(self):
            self._undo = []
        def setattr(self, target, name, value, raising=True):
            old = getattr(target, name)
            self._undo.append((target, name, old))
            setattr(target, name, value)
        def finalize(self):
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)

    # Tests with no fixtures.
    test_audio_mime_mapping()
    test_report_id_from_payload_falls_back_when_no_report_path(
        Path(tempfile.mkdtemp(prefix="avt_")),
    )

    # tmp_path-only tests.
    fixtures = [
        test_fingerprint_is_stable_and_content_keyed,
        test_record_from_response_normalizes_iso,
        test_get_or_create_jonny_character_creates_when_no_cache,
        test_get_or_create_jonny_character_uses_ready_cache,
        test_get_or_create_jonny_character_polls_processing_cache,
        test_build_avatar_track_dry_run,
        test_build_avatar_track_skips_missing_audio,
        test_list_reference_candidates_handles_missing_dir,
    ]
    for fn in fixtures:
        td = Path(tempfile.mkdtemp(prefix="avt_"))
        try:
            fn(td)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    # tmp_path + monkeypatch tests.
    monkey_fixtures = [
        test_avatar_animator_caches_per_audio_content,
        test_build_avatar_track_live_path_with_mocks,
    ]
    for fn in monkey_fixtures:
        td = Path(tempfile.mkdtemp(prefix="avt_"))
        mp = _MonkeyPatch()
        try:
            fn(td, mp)
        finally:
            mp.finalize()
            shutil.rmtree(td, ignore_errors=True)

    print("\nALL avatar pipeline tests passed.")


if __name__ == "__main__":
    _run_all()
