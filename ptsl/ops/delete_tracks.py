import json as _json

import ptsl.PTSL_pb2 as pt
from ptsl.ops import Operation


class DeleteTracks(Operation):
    @classmethod
    def command_id(cls):
        return pt.CId_DeleteTracks

    def json_messup(self, in_json: str) -> str:
        # Pro Tools rejects requests where both track_ids and track_names are
        # present. Strip whichever one is empty (same pattern as SetTrackColor).
        body = _json.loads(in_json)
        if not body.get("track_ids"):
            body.pop("track_ids", None)
        elif not body.get("track_names"):
            body.pop("track_names", None)
        return _json.dumps(body)
