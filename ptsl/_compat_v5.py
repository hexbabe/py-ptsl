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


def _vacated_name(track_name: str) -> str:
    """Internal name used for a pre-created empty track that was renamed aside."""
    return f"_v5_empty_{track_name}"


def spot_clips_by_id(
    engine,
    clip_ids: list[str],
    track_name: str,
    location_value: str = "0",
    color_index: int | None = None,
) -> None:
    """Emulate SpotClipsByID on PTSL v5 via Import + track rename.

    PT 2024.x does not support SpotClipsByID (PTSL v6+). We emulate it by:
      1. Renaming any pre-created empty destination track aside so its name is
         free for the import to use.
      2. Importing the audio file with ML_Spot, which causes PT to create a new
         track named after the file with the clip placed at location_value.
      3. Renaming that new stage track to the intended destination name.
      4. Hiding the vacated empty track so it does not clutter the session.
    """
    del color_index  # PT 2024.x has no clip-instance color API.

    _, registry = _session_registry(engine)
    by_id = registry["by_id"]

    for clip_id in clip_ids:
        entry = by_id.get(clip_id)
        if entry is None:
            raise KeyError(f"Unknown synthetic clip id: {clip_id}")

        # Step 1: If a pre-created destination track exists, rename it aside so
        # the import's stage track can later take the destination name.
        current_names = {t.name for t in engine.track_list()}
        vacated: str | None = None
        if track_name in current_names:
            vacated = _vacated_name(track_name)
            engine.rename_target_track(track_name, vacated)
            # Register as a stage track so filter_stage_tracks hides it from
            # subsequent track_list() calls inside this loop.
            _register_stage_track(registry, vacated)

        # Step 2: Import file with ML_Spot. PT 2024.x creates a new track named
        # after the file and places the clip at location_value.
        before_names = {t.name for t in engine.track_list()}
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

        # Step 3: Rename the new stage track to the destination name.
        after_names = [t.name for t in engine.track_list()]
        new_tracks = [n for n in after_names if n not in before_names]
        if new_tracks:
            stage = new_tracks[0]
            engine.rename_target_track(stage, track_name)
        else:
            # Import did not create a new track — restore the vacated track and
            # raise so the caller can retry or report failure.
            if vacated:
                try:
                    engine.rename_target_track(vacated, track_name)
                    _register_stage_track(registry, vacated)  # keep hidden
                except Exception:
                    pass
            raise RuntimeError(
                f"v5 spot: import did not create a stage track for {track_name!r}"
            )

        # Step 4: Hide the vacated empty track so it does not appear in the
        # session. It cannot be deleted on PTSL v5.
        if vacated:
            try:
                engine.set_track_hidden_state([vacated], True)
            except Exception:
                pass
