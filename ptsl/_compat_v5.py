"""
Compatibility helpers for PTSL v5 / Pro Tools 2024.x.

These shims let the high-level Engine keep exposing a 2025-style clip-list API
even though PT 2024.x does not support GetClipList / SpotClipsByID.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import shutil
from typing import Iterable

import ptsl.PTSL_pb2 as pt
from ptsl import ops


_REGISTRY: dict[str, dict[str, object]] = {}


def _normalize_tmp_path(path: str) -> str:
    """Prefer /tmp over /private/tmp for PT file imports on macOS."""
    if path == "/private/tmp":
        return "/tmp"
    if path.startswith("/private/tmp/"):
        return "/tmp/" + path[len("/private/tmp/"):]
    return path


def clear_registry() -> None:
    """Reset the in-memory clip registry used by the v5 shims."""
    _REGISTRY.clear()


def _session_key(engine) -> str:
    session_path = _normalize_tmp_path(engine.session_path())
    if not session_path:
        raise RuntimeError("No open Pro Tools session")
    return session_path


def _session_registry(engine) -> tuple[str, dict[str, object]]:
    session_key = _session_key(engine)
    registry = _REGISTRY.get(session_key)
    if registry is None:
        registry = {"by_id": {}, "by_source": {}, "stage_tracks": set()}
        _REGISTRY[session_key] = registry
    return session_key, registry


def _lookup_root(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0].lower()


def _source_key(path: str) -> str:
    return _normalize_tmp_path(os.path.abspath(path))


def _stable_ids(session_key: str, source_key: str) -> tuple[str, str]:
    digest = hashlib.sha1(
        f"{session_key}\0{source_key}".encode("utf-8")
    ).hexdigest()[:16]
    return (f"v5clip_{digest}", f"v5file_{digest}")


def _cache_dir(session_key: str) -> str:
    digest = hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:12]
    return f"/tmp/ptsl_v5_cache/{digest}"


def _copy_and_maybe_resample(
    file_list: list[str],
    session_audio_dir: str,
    session_sr: int,
) -> dict[str, str]:
    """Prepare files for PT 2024.x spotting.

    PT 2024.x behaves most reliably when the source file already lives in the
    session's Audio Files folder and matches the session sample rate.
    """
    import soundfile as _sf

    try:
        import librosa as _librosa
    except ImportError:
        _librosa = None

    os.makedirs(session_audio_dir, exist_ok=True)
    prepared: dict[str, str] = {}

    for src in file_list:
        source_key = _source_key(src)
        safe_name = os.path.basename(src).replace(" ", "_")
        source_dir = os.path.join(
            session_audio_dir,
            hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:12],
        )
        os.makedirs(source_dir, exist_ok=True)
        dst = os.path.join(source_dir, safe_name)
        dst = _normalize_tmp_path(dst)
        src_abs = os.path.abspath(src)
        dst_abs = os.path.abspath(dst)
        already_there = src_abs == dst_abs

        try:
            info = _sf.info(src)
            needs_resample = (
                isinstance(session_sr, int)
                and session_sr > 0
                and info.samplerate != session_sr
            )
        except Exception:
            needs_resample = False

        if needs_resample and _librosa is not None:
            data, _ = _librosa.load(src, sr=session_sr, mono=False)
            if getattr(data, "ndim", 1) > 1:
                data = data.T
            _sf.write(dst, data, session_sr, subtype="PCM_24")
        elif not already_there:
            shutil.copy2(src, dst)

        prepared[source_key] = dst

    return prepared


def import_audio_to_clip_list(
    engine,
    file_list: list[str],
    audio_operations: int | None = None,
    destination_path: str | None = None,
) -> pt.ImportAudioToClipListResponseBody:
    """Emulate CId_ImportAudioToClipList on PTSL v5.

    We register clip-like metadata and prepare session-local audio files now;
    the actual placement happens later inside spot_clips_by_id().
    """
    del audio_operations  # v5 shim always prepares AddAudio-compatible files.

    session_key, registry = _session_registry(engine)
    by_id = registry["by_id"]
    by_source = registry["by_source"]
    session_sr = engine.session_sample_rate()
    if not isinstance(session_sr, int):
        session_sr = 0

    del destination_path
    target_dir = _normalize_tmp_path(_cache_dir(session_key))

    response = pt.ImportAudioToClipListResponseBody()
    to_prepare: list[str] = []

    for path in file_list:
        source_key = _source_key(path)
        existing = by_source.get(source_key)
        if existing is None:
            to_prepare.append(path)

    prepared_paths = _copy_and_maybe_resample(to_prepare, target_dir, session_sr)

    for original_path in file_list:
        source_key = _source_key(original_path)
        entry = by_source.get(source_key)
        if entry is None:
            prepared_path = prepared_paths[source_key]
            clip_name = os.path.splitext(os.path.basename(original_path))[0]
            clip_id, file_id = _stable_ids(session_key, source_key)
            entry = {
                "name": clip_name,
                "root": clip_name,
                "lookup_root": _lookup_root(original_path),
                "source_key": source_key,
                "clip_id": clip_id,
                "file_id": file_id,
                "file_path": prepared_path,
            }
            by_id[clip_id] = entry
            by_source[source_key] = entry

        response_entry = response.file_list.add()
        response_entry.original_input_path = original_path
        dest_file = response_entry.destination_file_list.add()
        dest_file.file_id = entry["file_id"]
        dest_file.file_path = entry["file_path"]
        dest_file.clip_id_list.append(entry["clip_id"])

    return response


def get_clip_list(engine) -> list[pt.Clip]:
    """Return synthetic Clip messages for the v5 registry."""
    _, registry = _session_registry(engine)
    by_id = registry["by_id"]
    clips: list[pt.Clip] = []
    for entry in by_id.values():
        clips.append(pt.Clip(
            file_id=entry["file_id"],
            clip_id=entry["clip_id"],
            clip_full_name=entry["name"],
            clip_root_name=entry["root"],
            clip_type=pt.ClipType_Audio,
        ))
    return clips


def _register_stage_track(registry: dict[str, object], track_name: str) -> None:
    stage_tracks = registry.get("stage_tracks")
    if not isinstance(stage_tracks, set):
        stage_tracks = set()
        registry["stage_tracks"] = stage_tracks
    stage_tracks.add(track_name)


def filter_stage_tracks(engine, tracks: Iterable[pt.Track]) -> list[pt.Track]:
    """Hide shim-created staging tracks from the public v5 track list."""
    _, registry = _session_registry(engine)
    stage_tracks = registry.get("stage_tracks")
    if not isinstance(stage_tracks, set) or not stage_tracks:
        return list(tracks)

    return [track for track in tracks if track.name not in stage_tracks]


def _cleanup_extra_track(engine, registry: dict[str, object], track_name: str) -> None:
    _register_stage_track(registry, track_name)
    try:
        engine.set_track_hidden_state([track_name], True)
    except Exception:
        pass


@contextmanager
def _open_sibling_engine(engine):
    from ptsl import open_engine as _open_engine

    client = engine.client
    kwargs = {"address": getattr(client, "address", "localhost:31416")}
    certificate_path = getattr(client, "certificate_path", None)
    if certificate_path is not None:
        kwargs["certificate_path"] = certificate_path
    else:
        kwargs["company_name"] = getattr(client, "company_name", "py-ptsl")
        kwargs["application_name"] = getattr(
            client, "application_name", "py-ptsl")

    with _open_engine(**kwargs) as sibling_engine:
        yield sibling_engine


def _spot_stage_track_to_destination(
    engine,
    stage_track_name: str,
    destination_track_name: str,
    location_value: str,
) -> None:
    engine.select_tracks_by_name([stage_track_name])
    engine.select_all_clips_on_track(stage_track_name)
    engine.select_tracks_by_name([destination_track_name])
    op = ops.CId_Spot(
        track_offset_options=pt.Samples,
        location_data=pt.SpotLocationData(
            location_type=pt.Start,
            location_options=pt.Samples,
            location_value=str(location_value),
        ),
    )
    engine.client.run(op, timeout=12.0)


def _spot_stage_track_to_destination_fresh_engine(
    engine,
    stage_track_name: str,
    destination_track_name: str,
    location_value: str,
) -> None:
    with _open_sibling_engine(engine) as spot_engine:
        _spot_stage_track_to_destination(
            spot_engine,
            stage_track_name=stage_track_name,
            destination_track_name=destination_track_name,
            location_value=location_value,
        )


def _clear_stage_track_contents(stage_engine, stage_track_name: str) -> None:
    try:
        stage_engine.set_track_hidden_state([stage_track_name], False)
        stage_engine.select_tracks_by_name([stage_track_name])
        stage_engine.select_all_clips_on_track(stage_track_name)
        stage_engine.clear()
    finally:
        try:
            stage_engine.set_track_hidden_state([stage_track_name], True)
        except Exception:
            pass


def _clear_stage_track_contents_fresh_engine(
    engine,
    stage_track_name: str,
) -> None:
    try:
        with _open_sibling_engine(engine) as cleanup_engine:
            _clear_stage_track_contents(cleanup_engine, stage_track_name)
    except Exception:
        # Empty/undetected staging tracks should not fail the main spot path.
        pass


def spot_clips_by_id(
    engine,
    clip_ids: list[str],
    track_name: str,
    location_value: str = "0",
    color_index: int | None = None,
) -> None:
    """Emulate SpotClipsByID on PTSL v5 using the old Import command."""
    del color_index  # PT 2024.x has no clip-instance color API.

    _, registry = _session_registry(engine)
    by_id = registry["by_id"]

    for clip_id in clip_ids:
        entry = by_id.get(clip_id)
        if entry is None:
            raise KeyError(f"Unknown synthetic clip id: {clip_id}")

        before_tracks = [track.name for track in engine.track_list()]
        engine.select_tracks_by_name([track_name])
        audio_data = pt.AudioData(
            file_list=[entry["file_path"]],
            audio_operations=pt.AddAudio,
            audio_destination=pt.MD_None,
            audio_location=pt.ML_Spot,
            location_data=pt.SpotLocationData(
                location_type=pt.Start,
                location_options=pt.Samples,
                location_value=str(location_value),
            ),
        )
        op = ops.CId_Import(import_type=pt.Audio, audio_data=audio_data)
        engine.client.run(op, timeout=12.0)
        after_tracks = [track.name for track in engine.track_list()]
        extra_tracks = [
            name for name in after_tracks
            if name not in before_tracks and name != track_name
        ]
        for extra_track in extra_tracks:
            entry["stage_track_name"] = extra_track
            _cleanup_extra_track(engine, registry, extra_track)
        stage_track_name = entry.get("stage_track_name") or entry["name"]
        _spot_stage_track_to_destination_fresh_engine(
            engine,
            stage_track_name=stage_track_name,
            destination_track_name=track_name,
            location_value=str(location_value),
        )
        _clear_stage_track_contents_fresh_engine(engine, stage_track_name)
        engine.select_tracks_by_name([track_name])
