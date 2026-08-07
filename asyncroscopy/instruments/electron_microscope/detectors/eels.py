"""
EDS (Energy disperive X-ray spectroscopy) detector Tango device.

This device holds acquisition settings for the EDS detector.
It does NOT talk to AutoScript directly — the STEMMicroscope device
reads these attributes via DeviceProxy before acquiring.
"""

from tango import AttrWriteType, DevState
from tango.server import Device, attribute

class EELSBase(Device):
    hardware_host = device_property(
        dtype=str,
        default_value="10.46.217.242",
        doc="Hostname or IP of the Gatan server",
    )
    hardware_port = device_property(
        dtype=int,
        default_value=9092,
        doc="Port of the AutoScript microscope server",
    )

    # ------------------------------------------------------------------
    # Device properties — set per-deployment in the Tango DB
    # ------------------------------------------------------------------

    
    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------    
        
    exposure_time = attribute(
            label="Dwell Time",
            dtype=float,
            access=AttrWriteType.READ_WRITE,
            unit="s",
            format="%e",
            min_value=1e-6,
            max_value=5,
            doc="Exposure time in seconds (e.g. 1e-3 = 1 ms)",
        ) 
    number_of_frames = attribute(
                label="Number of Frames",
                dtype=float,
                access=AttrWriteType.READ_WRITE,
                unit="s",
                format="%e",
                min_value=1e-6,
                max_value=5,
                doc="Number of Frames to be summed over for spectrum e.g.: 1 or 10",
            ) 



    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------


    def init_device(self) -> None:
        Device.init_device(self)
        self.set_state(DevState.INIT)

         # Sensible defaults — operators override via Tango DB or client writes
        self._exposure_time: float = 1e-4  # 1 s
        self._number_of_frames: float = 1  # 1 frame

        self._message_id: int = 1
        self._last_status: str = "Uninitialised"

        self._connect()

    def _connect(self) -> None:
        return self.set_state(DevState.ON)
        
    # ------------------------------------------------------------------
    # Attribute read / write
    # ------------------------------------------------------------------

    def read_exposure_time(self) -> float:
        return self._exposure_time

    def write_exposure_time(self, value: float) -> None:
        self._exposure_time = value

    def read_number_of_frames(self) -> float:
            return self._number_of_frames
    
    def write_number_of_frames(self, value: float) -> None:
        self._number_of_frames = value
