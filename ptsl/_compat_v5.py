"""
Compatibility helpers for PTSL v5 / Pro Tools 2024.x.

These shims let the high-level Engine keep exposing a 2025-style clip-list API
even though PT 2024.x does not support GetClipList / SpotClipsByID.
"""

from __future__ import annotations

import hashlib
import os
import shutil

import ptsl.PTSL_pb2 as pt
from ptsl import ops


_REGISTRY: dict[str, dict[str, dict[str, dict[str, str]]]] = {}


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


def _session_registry(engine) -> tuple[str, dict[str, dict[str, dict[str, str]]]]:
    session_key = _session_key(engine)
    registry = _REGISTRY.get(session_key)
    if registry is None:
        registry = {"by_id": {}, "by_root": {}}
        _REGISTRY[session_key] = registry
    return session_key, registry


def _lookup_root(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0].lower()


def _stable_ids(session_key: str, lookup_root: str) -> tuple[str, str]:
    digest = hashlib.sha1(
        f"{session_key}\0{lookup_root}".encode("utf-8")
    ).hexdigest()[:16]
    return (f"v5clip_{digest}", f"v5file_{digest}")


def _cache_dir(session_key: str) -> str:
    digest = hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:12]
    return f"/tmp/ptsl_v5_cache/{digest}"


def _copy_and_maybe_resample(
    file_list: list[str],
    session_audio_dir: str,
    session_sr: int,
) -> list[str]:
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
    prepared: list[str] = []

    for src in file_list:
        safe_name = os.path.basename(src).replace(" ", "_")
        dst = os.path.join(session_audio_dir, safe_name)
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

        prepared.append(dst)

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
    session_sr = engine.session_sample_rate()
    if not isinstance(session_sr, int):
        session_sr = 0

    del destination_path
    target_dir = _normalize_tmp_path(_cache_dir(session_key))

    response = pt.ImportAudioToClipListResponseBody()
    to_prepare: list[str] = []
    prepared_meta: list[tuple[str, str]] = []

    for path in file_list:
        lookup_root = _lookup_root(path)
        existing = registry["by_root"].get(lookup_root)
        if existing is None:
            to_prepare.append(path)
            prepared_meta.append((path, lookup_root))

    prepared_paths = _copy_and_maybe_resample(to_prepare, target_dir, session_sr)
    prepared_iter = iter(prepared_paths)

    for original_path in file_list:
        lookup_root = _lookup_root(original_path)
        entry = registry["by_root"].get(lookup_root)
        if entry is None:
            prepared_path = next(prepared_iter)
            clip_name = os.path.splitext(os.path.basename(prepared_path))[0]
            clip_id, file_id = _stable_ids(session_key, lookup_root)
            entry = {
                "name": clip_name,
                "root": clip_name,
                "lookup_root": lookup_root,
                "clip_id": clip_id,
                "file_id": file_id,
                "file_path": prepared_path,
            }
            registry["by_id"][clip_id] = entry
            registry["by_root"][lookup_root] = entry
            registry["by_root"].setdefault(clip_name.lower(), entry)

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
    clips: list[pt.Clip] = []
    for entry in registry["by_id"].values():
        clips.append(pt.Clip(
            file_id=entry["file_id"],
            clip_id=entry["clip_id"],
            clip_full_name=entry["name"],
            clip_root_name=entry["root"],
            clip_type=pt.ClipType_Audio,
        ))
    return clips


def _cleanup_extra_track(engine, track_name: str) -> None:
    try:
        engine.select_all_clips_on_track(track_name)
        engine.clear()
    except Exception:
        pass

    try:
        engine.set_track_hidden_state([track_name], True)
    except Exception:
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

    for clip_id in clip_ids:
        entry = registry["by_id"].get(clip_id)
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
            _cleanup_extra_track(engine, extra_track)
