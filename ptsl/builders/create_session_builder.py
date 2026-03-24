import os
import time

from ptsl.PTSL_pb2 import SAF_AIFF, SAF_WAVE, \
    SR_48000, Bit16, Bit24, Bit32Float, \
    IO_Last, IO_StereoMix, IO_51SMPTEMix

import ptsl
from ptsl import ops, util
from ptsl.errors import CommandError


class CreateSessionBuilder:
    def __init__(self, engine: 'ptsl.Engine', name: str, path: str):
        self._engine = engine
        self._session_name = name
        self._path = path
        self._audio_format = SAF_WAVE
        self._sample_rate = SR_48000
        self._bit_depth = Bit24
        self._io_settings = IO_Last
        self._is_interleaved = False

    def audio_format(self, value: str):
        """
        :param value: Audio format for the new session. Acceptable
            values are "wave" or "aiff".
        """
        if value == 'wave':
            self._audio_format = SAF_WAVE
        elif value == 'aiff':
            self._audio_format = SAF_AIFF
        else:
            assert False, f"Invalid audio_format value {value}"

    def wave_format(self):
        self.audio_format("wave")

    def aiff_format(self):
        self.audio_format("aiff")

    def sample_rate(self, value: int):
        self._sample_rate = util.sample_rate_enum(value)

    def bit_depth(self, value: int):
        """
        :param value: Bit depth for the new session. Acceptable
            values are `16`, `24` or `32`.
        """
        if value == 16:
            self._bit_depth = Bit16
        elif value == 24:
            self._bit_depth = Bit24
        elif value == 32:
            self._bit_depth = Bit32Float
        else:
            assert False, f"Invalid bit_depth value {value}"

    def stereo_io_settings(self):
        self._io_settings = IO_StereoMix

    def smpte51_io_settings(self):
        self._io_settings = IO_51SMPTEMix

    def interleaved(self, value: bool):
        self._is_interleaved = value

    def _session_matches_target(self,
                                timeout_s: float = 3.0,
                                poll_s: float = 0.25) -> bool:
        requested_root = os.path.realpath(self._path)
        deadline = time.time() + timeout_s

        while time.time() < deadline:
            try:
                current_name = self._engine.session_name()
                current_path = self._engine.session_path()
                # If this succeeds, the session is not merely "transitioning";
                # PT considers it open enough for normal queries.
                self._engine.session_sample_rate()
            except Exception:
                time.sleep(poll_s)
                continue

            current_real = os.path.realpath(current_path)
            if current_name == self._session_name and current_real.startswith(requested_root):
                return True

            time.sleep(poll_s)

        return False

    def create(self) -> None:
        op = ops.CId_CreateSession(
            session_name=self._session_name,
            file_type=self._audio_format,
            sample_rate=self._sample_rate,
            input_output_settings=self._io_settings,
            is_interleaved=self._is_interleaved,
            session_location=self._path,
            bit_depth=self._bit_depth,
            is_cloud_project=False,
            create_from_template=False,
        )
        try:
            self._engine.client.run(op)
        except CommandError as exc:
            # PT 2024.x occasionally returns PT_InvalidTask after the session
            # is already open, and a naive retry then trips OS_DuplicateName.
            if self._session_matches_target():
                return
            raise exc


class CreateSessionFromTemplateBuilder(CreateSessionBuilder):

    def __init__(self, engine: 'ptsl.Engine',
                 template_name: str,
                 template_group: str,
                 name: str,
                 path: str):
        self._template_name = template_name
        self._template_group = template_group
        super().__init__(engine, name, path)

    def create(self):
        op = ops.CId_CreateSession(
            session_name=self._session_name,
            create_from_template=True,
            template_group=self._template_group,
            template_name=self._template_name,
            file_type=self._audio_format,
            sample_rate=self._sample_rate,
            input_output_settings=self._io_settings,
            is_interleaved=self._is_interleaved,
            session_location=self._path,
            bit_depth=self._bit_depth
        )

        self._engine.client.run(op)


class CreateSessionFromAAFBuilder(CreateSessionBuilder):

    def __init__(self, engine: 'ptsl.Engine',
                 aaf_path: str,
                 name: str,
                 path: str):
        self._aaf_path = aaf_path
        super().__init__(engine, name, path)

    def create(self):
        op = ops.CId_CreateSession(
            session_name=self._session_name,
            file_type=self._audio_format,
            sample_rate=self._sample_rate,
            input_output_settings=self._io_settings,
            is_interleaved=self._is_interleaved,
            session_location=self._path,
            bit_depth=self._bit_depth,
            create_from_aaf=True,
            path_to_aaf=self._aaf_path
        )

        self._engine.client.run(op)
