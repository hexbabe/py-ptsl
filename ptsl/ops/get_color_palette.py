import ptsl.PTSL_pb2 as pt
from ptsl.ops import Operation


class GetColorPalette(Operation):
    """Get the Pro Tools color palette for a given target type."""

    @classmethod
    def command_id(cls):
        return pt.CId_GetColorPalette
