import json as _json

import ptsl.PTSL_pb2 as pt
from ptsl.ops import Operation


class CId_CreateNewTracks(Operation):
    def json_messup(self, in_json: str) -> str:
        body = _json.loads(in_json)

        # Strip insertion_point fields at their unknown/empty defaults.
        # Some Pro Tools versions reject the request when these are present.
        if body.get("insertion_point_position") == "TIPoint_Unknown":
            body.pop("insertion_point_position", None)
        if not body.get("insertion_point_track_name"):
            body.pop("insertion_point_track_name", None)

        # Convert string enum values to integers. Older PTSL server
        # implementations don't accept string names (e.g. "AudioTrack") and
        # silently default the field to 0 ("Unknown track type: 0").
        _enum_fields = {
            "track_type": pt.TrackType,
            "track_format": pt.TrackFormat,
            "track_timebase": pt.TrackTimebase,
        }
        for field, enum_type in _enum_fields.items():
            val = body.get(field)
            if isinstance(val, str):
                try:
                    body[field] = enum_type.Value(val)
                except ValueError:
                    pass

        return _json.dumps(body)
