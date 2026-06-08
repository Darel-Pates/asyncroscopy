import json
import time
from typing import Optional


from abc import abstractmethod, ABC, ABCMeta

import numpy as np
import tango
from tango import AttrWriteType, DevEncoded, DevState, DevVarFloatArray, DevFloat
from tango.server import Device, DeviceMeta, attribute, command, device_property

from Microscope import Microscope

class SPMMicroscope(Microscope):
    """
    Top-level SPM microscope device.
    """
    # ------------------------------------------------------------------
    # Device properties — configure in Tango DB per deployment
    # ------------------------------------------------------------------

    scan_device_address = device_property(
        dtype=str,
        doc="Tango device address for the SCAN settings device. "
            "DB mode: 'test/scan' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/scan#dbase=no'",
    )

    approach_device_address = device_property(
        dtype=str,
        doc="Tango device address for the APPROACH settings device. "
            "DB mode: 'test/approach' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/approach#dbase=no'",
    )

    feedback_device_address = device_property(
        dtype=str,
        doc="Tango device address for the FEEDBACK settings device. "
            "DB mode: 'test/feedback' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/feedback#dbase=no'",
    )

    piezostage_device_address = device_property(
        dtype=str,
        doc="Tango device address for the PIEZOSTAGE settings device. "
            "DB mode: 'test/piezostage' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/piezostage#dbase=no'",
    )

    safety_device_address = device_property(
        dtype=str,
        doc="Tango device address for the SAFETY settings device. "
            "DB mode: 'test/safety' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/safety#dbase=no'",
    )

    stage_device_address = device_property(
        dtype=str,
        doc="Tango device address for the STAGE settings device. "
            "DB mode: 'test/stage' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/stage#dbase=no'",
    )

    spectroscopy_device_address = device_property(
        dtype=str,
        doc="Tango device address for the SPECTROSCOPY settings device. "
            "DB mode: 'test/spectroscopy' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/spectroscopy#dbase=no'",
    )

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _connect(self):
        print(f"Must define a class-specific _connect() method")

    def _disconnect(self):
        print(f"Must define a class-specific _disconnect() method")

    def _init_subclass_attributes(self) -> None:
        #TODO
        pass

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @command(dtype_in=str, dtype_out=DevEncoded) #????
    def get_spectrum(self,) -> tuple[str, bytes]:
        """
        """
        pass

    @command(dtype_in=str, dtype_out=DevEncoded)
    def acquire_scan(self, ) -> tuple[str, bytes]:
        """
        """
        pass

    @command(dtype_in=DevVarFloatArray, dtype_out=None)
    def move_probe(self, position) ->None:
        """
        """
        pass

    @command(dtype_in=DevVarFloatArray, dtype_out=None)
    def move_stage(self, position) -> None:
        """
        """
        pass



