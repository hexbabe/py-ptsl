import json as _json

import ptsl.PTSL_pb2 as pt
from ptsl.ops import Operation


class SpotClipsByID(Operation):
    @classmethod
    def command_id(cls):
        return pt.CId_SpotClipsByID

    def json_messup(self, in_json: str) -> str:
        # Pro Tools requires exactly one of dst_track_id / dst_track_name.
        # always_print_fields_with_no_presence serialises both; strip the unused one.
        body = _json.loads(in_json)
        if body.get("dst_track_name"):
            body.pop("dst_track_id", None)
        elif body.get("dst_track_id"):
            body.pop("dst_track_name", None)

        # Strip empty/default scalar fields from dst_location_data that were
        # forced in by always_print_fields_with_no_presence and would confuse
        # Pro Tools when the TimelineLocation message is used instead.
        loc = body.get("dst_location_data")
        if loc:
            if not loc.get("location_value"):
                loc.pop("location_value", None)
            if loc.get("location_options") in (None, "", "TOOptions_Unknown"):
                loc.pop("location_options", None)

        return _json.dumps(body)
