"""
Scanning Probe Microscope Tango device.

Sub-Devices settings are read from the corresponding DeviceProxy
so that each device is the single source of truth for its own params.

Image commands return a string supplied by the concrete microscope
implementation, typically a DATA/Tiled unique id.
"""

import json
from abc import abstractmethod
from typing import Optional

import tango

# from tango import AttrWriteType, DevEncoded, DevFloat, DevState, DevVarFloatArray, DevVarStringArray
# from tango.server import attribute, command, device_property

from asyncroscopy.instruments.instrument import Instrument

class SPMMode(enum.IntEnum):
    CONTACT_AFM         = 0
    NON_CONTACT_AFM     = 1
    KPFM                = 2
    EFM                 = 3
    CONDUCTIVE_AFM      = 4
    SF_PFM              = 5
    DART                = 6
    ESM                 = 7
    MFM                 = 8
    THERMAL             = 9
    AFM_IR              = 10
    TERS                = 11
    SNOM                = 12

class SPMMicroscope(Instrument):
    """
    Top-level scanning probe microscope device.
    """

    # ------------------------------------------------------------------
    # SPM subDevices — configure in Tango DB per deployment
    # ------------------------------------------------------------------

    scan_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the SCAN settings device. "
            "DB mode: 'test/scan' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/scan#dbase=no'",
    )

    approach_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the APPROACH settings device. "
            "DB mode: 'test/approach' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/approach#dbase=no'",
    )

    feedback_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the FEEDBACK settings device. "
            "DB mode: 'test/feedback' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/feedback#dbase=no'",
    )

    piezostage_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the PIEZOSTAGE settings device. "
            "DB mode: 'test/piezostage' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/piezostage#dbase=no'",
    )

    stage_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the STAGE settings device. "
            "DB mode: 'test/stage' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/stage#dbase=no'",
    )

    spectroscopy_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the SPECTROSCOPY settings device. "
            "DB mode: 'test/spectroscopy' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/spectroscopy#dbase=no'",
    )

    camera_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the CAMERA settings device. "
            "DB mode: 'test/camera' "
            "No-DB mode: 'tango://127.0.0.1:8888/test/nodb/camera#dbase=no'",
    )

    # ------------------------------------------------------------------
    # SPM Attributes
    # ------------------------------------------------------------------

    spm_mode = tango.server.attribute(
        label="SPM Mode",
        dtype=SPMMode,
        access=tango.AttrWriteType.READ,
        doc="Returns active SPM mode",
    )

    # ------------------------------------------------------------------
    # SPM-level Methods 
    # ------------------------------------------------------------------

    def _init_device_attributes(self) -> None:
        pass

    def read_instrument_type(self) -> str:
        return 'SPM'
    
    def read_spm_mode(self) -> SPMMode:
        return self._get_spm_mode()
    
    def _disconnect(self):
        self._microscope = None
        self.info_stream('Disconnected from microscope hardware')


    # ------------------------------------------------------------------
    # SPM Commands 
    # ------------------------------------------------------------------

    #spm level

    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def get_microscope_state(self,) -> str:
        #TODO
        #get active spm mode
        spm_mode = self.read_spm_mode()

        state = {
            "spm_mode": spm_mode.name 
        }
        
        return json.dumps(state)
    
    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def get_meter_values(self,) -> str:
        """Read current meter values: Sum, Defl, Lat, etc."""
        #TODO
        pass
        return ""

    #high_level

    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def acquire_scan(self,) -> str:
        pass #TODO
        return ""
    
    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def acquire_spectrum(self,) -> str:
        pass #TODO
        return ""

    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def move_probe(self,) -> str:
        pass #TODO
        return ""

    #stage

    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def move_stage(self,) -> str:
        pass #TODO
        return ""
    
    #feedback_control

    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def feedback_on(self,) -> str:
        pass #TODO
        return ""
    
    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def feedback_off(self,) -> str:
        pass #TODO
        return ""
    
    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def set_set_point(self,) -> str:
        pass #TODO
        return ""
    
    #approach

    @tango.command(dtype_in=str, dtype_out=tango.DevEncoded)
    def approach(self,) -> str:
        pass #TODO
        return ""
    

    # ------------------------------------------------------------------
    # SPM Abstract Methods 
    # ------------------------------------------------------------------
    
    @abstractmethod
    def _connect(self):
        pass

    @abstractmethod
    def _get_spm_mode(self)-> SPMMode:
        """Get active spm mode"""
        pass

if __name__ == '__main__':
    SPMMicroscope.run_server()

