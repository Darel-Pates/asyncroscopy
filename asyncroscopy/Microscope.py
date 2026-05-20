"""
Microscope Tango device.

Detector settings are read from the corresponding detector DeviceProxy
so that each detector device is the single source of truth for its own params.

Return convention for image commands
-------------------------------------
Image commands return a string. Hardware-backed microscopes should save the
native adorned object on disk and return the saved path for the Tiled server.
"""

import json
import time
from typing import Optional

from abc import abstractmethod, ABCMeta

import tango
from tango import AttrWriteType, DevEncoded, DevState, DevVarFloatArray, DevFloat
from tango.server import Device, DeviceMeta, attribute, command, device_property

class CombinedMeta(DeviceMeta, ABCMeta):
    """Combines Tango DeviceMeta and ABCMeta to allow abstract methods in Devices."""
    pass

class Microscope(Device, metaclass=CombinedMeta):
    """
    Top-level TEM microscope device.
    Detector-specific settings (dwell time, resolution) are stored in
    dedicated detector devices and read via DeviceProxy at acquisition time.
    """

    # ------------------------------------------------------------------
    # Device properties — configure in Tango DB per deployment
    # ------------------------------------------------------------------

    scan_device_address = device_property(
        dtype=str,
        doc="Tango device address for the SCAN settings device. "
            "DB mode: 'asyncroscopy/scan/default' "
            "No-DB mode: 'tango://127.0.0.1:8888/asyncroscopy/scan/default#dbase=no'",
    )

    eds_device_address = device_property(
        dtype=str,
        doc="Tango device address for the EDS settings device. "
            "DB mode: 'asyncroscopy/eds/default' "
            "No-DB mode: 'tango://127.0.0.1:8887/asyncroscopy/haadf/default#dbase=no'",
    )

    stage_device_address = device_property(
        dtype=str,
        doc="Tango device address for the STAGE settings device. "
            "DB mode: 'asyncroscopy/stage/default' "
            "No-DB mode: 'tango://127.0.0.1:8888/asyncroscopy/stage/default#dbase=no'",
    )

    camera_device_address = device_property(
        dtype=str,
        doc="Tango device address for the CAMERA settings . "
            "DB mode: 'asyncroscopy/camera/default' "
            "No-DB mode: 'tango://127.0.0.1:8888/asyncroscopy/camera/default#dbase=no'",
    )

    flucam_device_address = device_property(
        dtype=str,
        default_value="",
        doc="Tango device address for the FLUCAM settings device. "
            "DB mode: 'asyncroscopy/flucam/default' "
            "No-DB mode: 'tango://127.0.0.1:8888/asyncroscopy/flucam/default#dbase=no'",
    )
    testing_mode_bool = device_property(dtype=bool, 
                                        default_value=False,
                                        doc="When True - used for running tests, passed in conftest.py")

    # Add further detector device_property entries here as detectors are added
    # eels_device_address  = device_property(dtype=str, default_value="asyncroscopy/eels/default")

    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------

    stem_mode = attribute(
        label="STEM Mode",
        dtype=bool,
        access=AttrWriteType.READ,
        doc="True when the microscope is in STEM mode",
    )

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def init_device(self) -> None:
        Device.init_device(self)
        self.set_state(DevState.INIT)

        self._microscope: Optional[object] = None  # TemMicroscopeClient instance
        self._stem_mode: bool = False

        # Dict mapping detector name string → DeviceProxy
        # Populated in _connect_detector_proxies
        self._detector_proxies: dict[str, tango.DeviceProxy] = {}

        self._connect()

    @abstractmethod
    def _connect(self):
        pass
    
    @abstractmethod
    def _connect_hardware(self) -> None:
        pass

    @abstractmethod
    def _connect_detector_proxies(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Attribute read methods
    # ------------------------------------------------------------------

    def read_stem_mode(self) -> bool:
        # TODO: query self._microscope.optics.mode when AutoScript available
        return self._stem_mode

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @command
    def Connect(self) -> None:
        """Explicitly (re)connect to microscope hardware. Useful after a fault.
        Also, sets the timeout fofr Tango device for 2 minutes (for larger things)
        """
        self._connect()

    @command
    def Disconnect(self) -> None:
        """Disconnect from microscope hardware gracefully."""
        # TODO: self._microscope.disconnect() when AutoScript available
        self._microscope = None
        self.set_state(DevState.OFF)
        self.info_stream("Disconnected from microscope hardware")

    @command(dtype_in=str, dtype_out=str)
    def configure_tiled_acquisition(self, config_json: str) -> str:
        """Configure where acquisitions are saved before the Tiled server serves them."""
        return self._configure_tiled_acquisition(config_json)

    @command(dtype_out=str)
    def get_tiled_acquisition_config(self) -> str:
        """Return the active Tiled acquisition config as JSON."""
        return self._get_tiled_acquisition_config()

    @command(dtype_in=str, dtype_out=DevEncoded)
    def get_spectrum(self, detector_name: str) -> tuple[str, bytes]:
        """
        Acquire a single spectrum from the named detector with the specified exposure time.

        Parameters
        ----------
        detector_name:
            Name of the detector, e.g. "eds".

        Returns
        -------
        DevEncoded = (json_metadata, raw_bytes)
            json_metadata includes: shape, dtype, dwell_time, detector,
            timestamp, and any other relevant metadata.
            raw_bytes is the flat numpy array bytes; reshape using shape from metadata.
        """

        detector_name = detector_name.lower().strip()
        proxy = self._detector_proxies.get(detector_name)
        if proxy is None:
            available = ", ".join(sorted(self._detector_proxies.keys())) or "none"
            tango.Except.throw_exception(
                "UnknownDetector",
                (
                    f"Detector '{detector_name}' is not configured or connected. "
                    f"Available detectors: {available}"
                ),
                "get_spectrum()",
            )

        # Read acquisition settings from the detector device
        exposure_time = proxy.exposure_time # float

        adorned_spectrum = self._acquire_spectrum(detector_name, exposure_time)

        metadata = {
            "detector": detector_name,
            "dwell_time": exposure_time,
            "timestamp": time.time(),
            # TODO: add metadata from adorned_spectrum.metadata when using real AutoScript
        }

        if isinstance(adorned_spectrum, dict):
            raw_bytes = json.dumps(adorned_spectrum).encode("utf-8")
        else:
            raw_bytes = adorned_spectrum.tobytes()

        return json.dumps(metadata), raw_bytes

    @command(dtype_out=str)
    def get_scanned_image(self) -> str:
        """Acquire a STEM image using settings from the scan device."""
        scan = self._detector_proxies.get("scan")
        if scan is None:
            self._raise_missing_detector("scan", "get_scanned_image()")

        result = self._acquire_stem_image(scan.imsize, scan.dwell_time, ["haadf"])
        if isinstance(result, str):
            return result

        self._cached_images = [result]
        img_data = result.data if hasattr(result, "data") else result
        return json.dumps({
            "detector": "haadf",
            "shape": list(img_data.shape),
            "dtype": str(img_data.dtype),
            "cache_index": 0,
            "data_command": "get_image_data_cached",
        })

    @command(dtype_out=str)
    def get_camera_image(self) -> str:
        """Acquire a camera image using settings from the camera device."""
        return self._get_configured_camera_image(
            proxy_name="camera",
            detector="BM-Ceta",
            origin="get_camera_image()",
        )

    @command(dtype_out=str)
    def get_flucam_image(self) -> str:
        """Acquire a Flucam image using settings from the flucam device."""
        return self._get_configured_camera_image(
            proxy_name="flucam",
            detector="Flucam",
            origin="get_flucam_image()",
        )

    @command(dtype_in=('str',), dtype_out=str)
    def get_images(self, detector_names: list[str]) -> str:
        """
        Acquire multiple STEM images simultaneously.

        Parameters
        ----------
        detector_names: list of detector names, e.g. ["HAADF", "BF"]

        Returns
        -------
        JSON string returned by the vendor-specific implementation. Hardware
        microscopes should return saved paths; simulators may return cache
        metadata for get_image_data_cached().
        """
        detector_names = [name.strip() for name in detector_names]
        scan = self._detector_proxies.get("scan")
        if scan is None:
            self._raise_missing_detector("scan", "get_images()")

        results = self._acquire_stem_image_advanced(
            imsize=scan.imsize,
            dwell_time=scan.dwell_time,
            detector_list=detector_names,
            scan_region=[0.0, 0.0, 1.0, 1.0],
        )

        if all(isinstance(result, str) for result in results):
            return json.dumps({"paths": results, "count": len(results)})

        self._cached_images = results
        metadata = []
        for index, (detector, image) in enumerate(zip(detector_names, results)):
            img_data = image.data if hasattr(image, "data") else image
            metadata.append({
                "index": index,
                "detector": detector,
                "shape": list(img_data.shape),
                "dtype": str(img_data.dtype),
            })
        return json.dumps({"images": metadata, "count": len(results)})

    @command(dtype_in=int, dtype_out=DevEncoded)
    def get_image_data_cached(self, index: int) -> tuple[str, bytes]:
        """Retrieve cached image by index."""
        if not hasattr(self, '_cached_images'):
            tango.Except.throw_exception("NoCache", "Call get_images() first", "get_image_data()")
        if index >= len(self._cached_images):
            tango.Except.throw_exception("InvalidIndex", f"Index {index} out of range", "get_image_data()")
        
        adorned_img = self._cached_images[index]
        img_data = adorned_img.data if hasattr(adorned_img, 'data') else adorned_img
        
        meta = {"shape": list(img_data.shape), "dtype": str(img_data.dtype)}
        return json.dumps(meta), img_data.tobytes()

    def _raise_missing_detector(self, detector_name: str, origin: str) -> None:
        available = ", ".join(sorted(self._detector_proxies.keys())) or "none"
        tango.Except.throw_exception(
            "UnknownDetector",
            (
                f"Detector '{detector_name}' is not configured or connected. "
                f"Available detectors: {available}"
            ),
            origin,
        )

    def _get_configured_camera_image(self, proxy_name: str, detector: str, origin: str) -> str:
        camera = self._detector_proxies.get(proxy_name)
        if camera is None:
            self._raise_missing_detector(proxy_name, origin)

        return self._acquire_camera_image(
            imsize=camera.imsize,
            exposure_time=camera.exposure_time,
            detector=detector,
            readout_area=camera.readout_area,
        )
    
    @command(dtype_in=DevVarFloatArray, dtype_out=None)
    def place_beam(self, position) -> None:
        """
        sets resting beam position, [0:1]
        """
        self._place_beam(position)

    @command()
    def blank_beam(self) -> None:
        """blank beam"""
        self._blank_beam()

    @command()
    def unblank_beam(self) -> None:
        """
        unblank beam
        """
        self._unblank_beam()

    @command(dtype_in=DevFloat)
    def set_fov(self, fov):
        """
        set the field of view for the next acquisition
        """
        self._set_fov(fov)

    @command(dtype_out=DevFloat)
    def get_fov(self):
        """
        read the field of view for the next acquisition
        """
        return self._get_fov()
    
    @command(dtype_in=DevFloat)
    def set_screen_current(self, current):
        """
        set the screen current in pA
        """
        self._set_screen_current(current)

    @command(dtype_out=DevFloat)
    def get_screen_current(self):
        """
        get the screen current in pA
        """
        return self._get_screen_current()

    @command(dtype_out=DevVarFloatArray)
    def get_stage(self):
        """
        Get the current stage position as a list of floats [x, y, z, alpha, beta].

        Returns
        -------
        DevVarFloatArray = [x, y, z, alpha, beta]

        """
        position = self._get_stage()

        return position

    @command(dtype_in=DevVarFloatArray)
    def move_stage(self, position):
        """
        Move the the stage
        to an absolute position  [x, y, z, alpha, beta]

        Parameters
        position: an absolute reference frame move position (not relative)

        """
        self._move_stage(position)

    @command()
    def auto_focus(self):
        """
        Run the microscope's autofocus routine.
        """
        self._auto_focus()

    @command(dtype_in=DevVarFloatArray)
    def set_image_shift(self, shift):
        """
        Set the image shift to the specified values [x_shift, y_shift].

        Parameters
        ----------
        shift: list of two floats [x_shift, y_shift] specifying the desired image shift in meters.
        """
        self._set_image_shift(shift)
    # ------------------------------------------------------------------
    # Internal acquisition helpers
    # ------------------------------------------------------------------
    def _configure_tiled_acquisition(self, config_json: str) -> str:
        tango.Except.throw_exception(
            "UnsupportedCommand",
            "This microscope does not support Tiled acquisition configuration.",
            "_configure_tiled_acquisition()",
        )

    def _get_tiled_acquisition_config(self) -> str:
        tango.Except.throw_exception(
            "UnsupportedCommand",
            "This microscope does not support Tiled acquisition configuration.",
            "_get_tiled_acquisition_config()",
        )

    @abstractmethod
    def _acquire_stem_image(self, imsize: int, dwell_time: float, detector_list: list[str]):
        """Vendor-specific STEM acquisition implementation."""
        pass

    def _acquire_camera_image(self, imsize: int, exposure_time: float, detector: str, readout_area: str) -> str:
        """Vendor-specific camera acquisition implementation."""
        tango.Except.throw_exception(
            "UnsupportedCommand",
            "This microscope does not support camera image acquisition.",
            "_acquire_camera_image()",
        )

    @abstractmethod
    def _acquire_stem_image_advanced(
        self,
        imsize: int,
        dwell_time: float,
        detector_list: list[str],
        scan_region: list[float],
    ) -> list:
        """Vendor-specific multi-image acquisition implementation."""
        pass

    def _place_beam(self, position):
        # define in the inherit class
        pass

    def _blank_beam(self):
        # define in the inherit class
        pass

    def _unblank_beam(self):
        # define in the inherit class
        pass

    @abstractmethod
    def _set_screen_current(self, current):
        # define in the inherit class
        pass

    @abstractmethod
    def _get_screen_current(self):
        pass

    @abstractmethod
    def _move_stage(self, position):
        # define in the inherit class
        pass

    @abstractmethod
    def _get_stage(self):
        pass

    @abstractmethod
    def _set_fov(self, fov):
        pass

    @abstractmethod
    def _get_fov(self):
        pass

    @abstractmethod
    def _auto_focus(self):
        pass

    @abstractmethod
    def _set_image_shift(self, shift):
        pass
# ----------------------------------------------------------------------
# Server entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    Microscope.run_server()
