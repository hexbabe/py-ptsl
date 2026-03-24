import json as _json

from ptsl.ops import Operation


_TRACK_TYPE_V6_TO_V5 = {
    "Unknown": "TT_Unknown",
    "Midi": "TT_Midi",
    "AudioTrack": "TT_Audio",
    "Aux": "TT_Aux",
    "VideoTrack": "TT_Video",
    "Vca": "TT_Vca",
    "Tempo": "TT_Tempo",
    "Markers": "TT_Markers",
    "Meter": "TT_Meter",
    "KeySignature": "TT_KeySignature",
    "ChordSymbols": "TT_ChordSymbols",
    "Instrument": "TT_Instrument",
    "Master": "TT_Master",
    "Heat": "TT_Heat",
    "BasicFolder": "TT_BasicFolder",
    "RoutingFolder": "TT_RoutingFolder",
    "CompLane": "TT_CompLane",
}


class CId_CreateNewTracks(Operation):
    def json_messup(self, in_json: str) -> str:
        body = _json.loads(in_json)

        # Strip insertion_point fields at their unknown/empty defaults.
        # Some Pro Tools versions reject the request when these are present.
        if body.get("insertion_point_position") in ("TIPoint_Unknown", 0):
            body.pop("insertion_point_position", None)
        if not body.get("insertion_point_track_name"):
            body.pop("insertion_point_track_name", None)

        return _json.dumps(body)

    def json_messup_for_version(self,
                                in_json: str,
                                server_version: int) -> str:
        body = _json.loads(self.json_messup(in_json))

        if server_version < 6:
            if "pagination_request" not in body:
                body["pagination_request"] = {"limit": 1000, "offset": 0}

            track_type = body.get("track_type")
            if track_type in _TRACK_TYPE_V6_TO_V5:
                body["track_type"] = _TRACK_TYPE_V6_TO_V5[track_type]

        return _json.dumps(body)
