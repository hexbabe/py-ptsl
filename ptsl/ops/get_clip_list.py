import json as _json

import ptsl.PTSL_pb2 as pt
from ptsl.ops import Operation


class GetClipList(Operation):
    @classmethod
    def command_id(cls):
        return pt.CId_GetClipList

    def json_cleanup(self, in_json: str) -> str:
        # Pro Tools returns "clip_list" but the proto field is named "clips".
        body = _json.loads(in_json)
        if "clip_list" in body:
            body["clips"] = body.pop("clip_list")
        return _json.dumps(body)
