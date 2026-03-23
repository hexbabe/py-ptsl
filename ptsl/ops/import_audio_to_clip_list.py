import json as _json

import ptsl.PTSL_pb2 as pt
from ptsl.ops import Operation


class ImportAudioToClipList(Operation):
    @classmethod
    def command_id(cls):
        return pt.CId_ImportAudioToClipList

    def json_messup(self, in_json: str) -> str:
        # Strip destination_path when empty to avoid confusing Pro Tools.
        body = _json.loads(in_json)
        if not body.get("destination_path"):
            body.pop("destination_path", None)
        return _json.dumps(body)
