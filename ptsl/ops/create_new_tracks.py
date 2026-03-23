import json as _json

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

        return _json.dumps(body)
