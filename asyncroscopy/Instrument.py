import json
from typing import Optional


from abc import abstractmethod, ABCMeta

import tango
from tango import AttrWriteType, DevEncoded, DevState, DevVarFloatArray, DevFloat, DevVarStringArray
from tango.server import Device, DeviceMeta, attribute, command, device_property


class CombinedMeta(DeviceMeta, ABCMeta):
    """Combines Tango DeviceMeta and ABCMeta to allow abstract methods in Devices."""
    pass

class Instrument(Device, metaclass=CombinedMeta):

    # ------------------------------------------------------------------
    # Device properties — configure in Tango DB per deployment
    # ------------------------------------------------------------------
    data_device_address = device_property(
        dtype=str,
        default_value="",
        doc="Optional Tango device address for the DATA device, e.g. 'asyncroscopy/data/default'.",
    )

    testing_mode_bool = device_property(
        dtype=bool, 
        default_value=False,
        doc="When True - used for running tests, passed in conftest.py")
    
    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------

    microscope_type = attribute(
        label="Instrument Type",
        dtype=str,
        access=AttrWriteType.READ,
        doc="Instrument modality, for example 'STEM', 'SPM', 'TEM', or 'OPTIC'.",
    )

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def init_device(self) -> None:
        Device.init_device(self)
        self.set_state(DevState.INIT)

        self._init_subclass_attributes()
        self._connect()

    # ------------------------------------------------------------------
    # Attribute read methods
    # ------------------------------------------------------------------
    @abstractmethod
    def read_instrument_type(self) -> str:
        pass

    # ------------------------------------------------------------------
    # Connection/disconnection methods
    # ------------------------------------------------------------------

    @abstractmethod
    def _connect(self):
        pass

    @abstractmethod
    def _disconnect(self):
        pass

    @abstractmethod
    def _init_subclass_attributes(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @command
    def Connect(self) -> None:
        """
        Explicitly (re)connect to microscope hardware. Useful after a fault.
        """
        self._connect()

    @command
    def Disconnect(self) -> None:
        """Disconnect from microscope hardware gracefully."""
        self.set_state(DevState.OFF)
        self._disconnect()

    


