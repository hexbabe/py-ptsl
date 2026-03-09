import json as _json

import ptsl.PTSL_pb2 as pt
from ptsl.ops import Operation


class SetTrackColor(Operation):
    @classmethod
    def command_id(cls):
        return pt.CId_SetTrackColor

    def json_messup(self, in_json: str) -> str:
        # always_print_fields_with_no_presence in the client serializes both
        # track_ids and track_names, but Pro Tools rejects requests where both
        # are present (PT_InvalidParameter). Strip the one we're not using.
        body = _json.loads(in_json)
        if not body.get("track_ids"):
            body.pop("track_ids", None)
        elif not body.get("track_names"):
            body.pop("track_names", None)
        return _json.dumps(body)
