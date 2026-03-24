import json as _json

from .operation import Operation

_UNKNOWN_DEFAULTS = {
    "import_type": {"Unknown", "IType_Unknown"},
    "audio_operations": {"AOperations_Unknown"},
    "destination": {"MDestination_Unknown"},
    "audio_destination": {"MDestination_Unknown"},
    "location": {"MLocation_Unknown"},
    "audio_location": {"MLocation_Unknown"},
}

_UNKNOWN_LOCATION_TYPES = {"SLType_Unknown"}
_UNKNOWN_LOCATION_OPTIONS = {"TOOptions_Unknown"}


def _strip_unknown_defaults(body: dict) -> dict:
    """Remove v6-only default enum strings that confuse PT 2024.x.

    The 2025-generated protobuf JSON includes zero-valued enum fields even when
    they were never meaningfully set. PT 2024.x does not recognize several of
    those symbolic names, so on v5 we omit them entirely and let the server use
    its own defaults.
    """
    for key, unknown_values in _UNKNOWN_DEFAULTS.items():
        if body.get(key) in unknown_values:
            body.pop(key, None)

    audio_data = body.get("audio_data")
    if not isinstance(audio_data, dict):
        return body

    for key, unknown_values in _UNKNOWN_DEFAULTS.items():
        if audio_data.get(key) in unknown_values:
            audio_data.pop(key, None)

    location_data = audio_data.get("location_data")
    if isinstance(location_data, dict):
        if location_data.get("location_type") in _UNKNOWN_LOCATION_TYPES:
            location_data.pop("location_type", None)
        if location_data.get("location_options") in _UNKNOWN_LOCATION_OPTIONS:
            location_data.pop("location_options", None)
        if location_data.get("location_value", "") == "":
            location_data.pop("location_value", None)
        if not location_data:
            audio_data.pop("location_data", None)

    return body


class CId_Import(Operation):
    def json_messup(self, in_json: str) -> str:
        body = _json.loads(in_json)

        if not body.get("session_path"):
            body.pop("session_path", None)

        audio_data = body.get("audio_data")
        if audio_data and not audio_data.get("destination_path"):
            audio_data.pop("destination_path", None)

        return _json.dumps(body)

    def json_messup_for_version(self,
                                in_json: str,
                                server_version: int) -> str:
        body = _json.loads(self.json_messup(in_json))

        if server_version < 6:
            body = _strip_unknown_defaults(body)

        return _json.dumps(body)
