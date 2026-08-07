
from tango import DevState
from tango.server import device_property


from tango import AttrWriteType, DevState, DevVarFloatArray
from tango.server import Device, attribute, command
import Pyro5.api

# from .eels import EELSBase

class EELSBase(Device):
    
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

    # ------------------------------------------------------------------
    # Public commands
    # ------------------------------------------------------------------
    
    @command(dtype_out=int)  
    def _initialize_eels(self) -> int:
        """ Initialize EELS mode and make sure eels server responds"""
        return 0

    @command(dtype_in=DevVarFloatArray)
    def set_eels_offset(self, offset):
        """ Set the eels energy offset in eV"""
        return self._set_eels_offset(offset)

    @command(dtype_out=str)
    def get_eels_spectrum(self):
        """ Get eels spectrum filename as key for tile server"""
        return self._get_eels_spectrum()

    @command(dtype_out=str)
    def get_available_dispersions(self):
        """Get all available dispersions in eV/channel and their index""" 
        return self._get_available_dispersions()

    @command(dtype_out=DevVarFloatArray)
    def get_eels_dispersion(self):
        """Get current dispersion in eV/channel"""
        return self._get_eels_dispersion()

    @command(dtype_in=DevVarFloatArray)                
    def set_eels_dispersion(self, dispersion_index):
        """Get current dispersion in eV/channel"""
        return self._set_eels_dispersion(dispersion_index)
                    
    @command(dtype_out=str)
    def get_eels_aperture(self):
        """Get current EELS entrance aperature as str and index"""
        return self._get_eels_aperture()

    @command(dtype_out=int)
    def set_eels_aperture(self, aperture_index):
        """Set EELS entrance aperature by its index"""
        return self._set_eels_aperture(self, aperture_index)

    @command(dtype_out=str)
    def get_available_apertures(self):
        """Get all available EELS entrance aperatures and their indices"""
        return self._get_available_apertures()

    
    # ------------------------------------------------------------------
    # Private Functions
    # ------------------------------------------------------------------

    def _set_eels_offset(self, offset):
        pass
    
    def _get_eels_spectrum(self):
        pass    

    def _get_available_dispersions(self):    
        pass

    def _get_eels_dispersion(self):
        pass

    def _set_eels_dispersion(self, dispersion_index):
        pass

    def _get_eels_aperture(self):
        pass

    def _set_eels_aperture(self, aperture_index):
        pass

    def _get_available_apertures(self):
        pass

class EELS(EELSBase):
    """EELS detector settings device."""

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
        """Verify TCP connectivity to the Gatan server and transition to ON."""
        uri = f"PYRO:eels_server@{self.hardware_host}:{self.hardware_port}"
        self._eels_proxy = Pyro5.api.Proxy(uri)
        if not self._eels_proxy.check_server():
            raise ConnectionError("EELS server not available")
        self.info_stream(
            f"EELS server reachable at {self.hardware_host}:{self.hardware_port}"
        )
        self._last_status = "Connected"
        self.set_state(DevState.ON)


    def _initialize_eels(self) -> bool:
        """ Initialize EELS mode and make sure eels server responds"""
        return self._eels_proxy.initialize_eels()
    

   
    def _set_eels_offset(self, offset):
        """ Set the eels energy offset in eV"""
        return self._eels_proxy.set_eels_offset(offset)

   
    def _get_eels_spectrum(self):
        """ Get eels spectrum filename as key for tile server"""
        spectrum, offset, dispersion = self.eels_proxy.get_eels_spectrum(self.exposure_time,
                                                                         self.number_of_frames)
        energy_scale =np.arange(len(spectrum)) * dispersion + offset
        spectrum = np.array(spectrum)
        metadata = {'offset': offset,
                    'dispersion': dispersion,
                    'exposure_time': self.exposure_time,
                    'nmber_of_frames': self.number_of_frames}
        # data_server = self._detector_proxies.get("data")
        # return save_acquisition(self, data_server, "eels_spectrum", str(Gatan ImageFilter), 
        #                        data=spectrum,
        #                        data_attributes=metadata, 
        #                        dataset_name="eels_data")
        


if __name__ == "__main__":
    EELS.run_server()