"""
Microscope Tango device.

Owns the AutoScript connection and all acquisition commands.
Detector settings are read from the corresponding detector DeviceProxy
so that each detector device is the single source of truth for its own params.

AutoScript is an optional dependency; this module imports cleanly without it
and falls back to simulated acquisition. To enable real hardware:

    pip install asyncroscopy[autoscript]

Return convention for real image commands
-----------------------------------------
Real AutoScript image commands save the adorned object on disk and return the
saved file path. The Tiled device can read that path.
"""

import math
import json
import time

import numpy as np
import tango
from tango import AttrWriteType, DevState
from tango.server import attribute, device_property

from asyncroscopy.Microscope import Microscope
from asyncroscopy.tiled_helpers import (
    DEFAULT_ACQUISITION_DIR,
    acquisition_config,
    save_adorned_acquisition,
)

# AutoScript imports — only available on the microscope PC.
# Wrapped in try/except so the device can still be imported and tested
# on a development machine without AutoScript installed.
try:
    from autoscript_tem_microscope_client import TemMicroscopeClient
    from autoscript_tem_microscope_client.enumerations import EdsDetectorType
    from autoscript_tem_microscope_client.enumerations import RegionCoordinateSystem, ExposureTimeType
    from autoscript_tem_microscope_client.structures import Region, Rectangle
    from autoscript_tem_microscope_client.structures import StemAcquisitionSettings, EdsAcquisitionSettings, RunOptiStemSettings, CameraAcquisitionSettings

    _AUTOSCRIPT_AVAILABLE = True
except ImportError:
    _AUTOSCRIPT_AVAILABLE = False

class ThermoMicroscope(Microscope):
    """
    Manages the AutoScript connection and exposes acquisition commands.
    Detector-specific settings (dwell time, resolution) are stored in
    dedicated detector devices and read via DeviceProxy at acquisition time.
    """

    # ------------------------------------------------------------------
    # Device properties — configure in Tango DB per deployment
    # ------------------------------------------------------------------
    autoscript_host_ip = device_property(
        dtype=str,
        default_value="10.46.217.241",
        doc="Hostname or IP of the AutoScript microscope server",
    )
    autoscript_host_port = device_property(
        dtype=int,
        default_value=9095,
        doc="Hostname or IP of the AutoScript microscope server",
    )
    acquisition_save_directory = device_property(
        dtype=str,
        default_value=DEFAULT_ACQUISITION_DIR,
        doc="Directory where AutoScript acquisitions are saved before Tiled serves them.",
    )
    acquisition_file_format = device_property(
        dtype=str,
        default_value="tiff",
        doc="Acquisition file format. TIFF preserves AutoScript image metadata.",
    )
    tiled_device_address = device_property(
        dtype=str,
        default_value="",
        doc="Optional Tango device address for the Tiled device, e.g. 'asyncroscopy/tiled/default'.",
    )

    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------
    # not finishded
    manufacturer = attribute(
        label="Thermofisher",
        dtype=bool,
        access=AttrWriteType.READ,
        doc="This microscope uses AutoScript for control and acquisition",
    )

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _connect(self):
        self._connect_hardware()
        self._connect_detector_proxies()
        self._ensure_tiled_acquisition_config()
        self.set_state(DevState.ON)
        self.screen_current_calibration = None

    def _connect_hardware(self) -> None:
        """Establish AutoScript connection from MPC -> hardware."""
        if not _AUTOSCRIPT_AVAILABLE or self.testing_mode_bool:
            self.warn_stream("AutoScript not available")
            return
        try:
            self._microscope = TemMicroscopeClient()
            self._microscope.connect(self.autoscript_host_ip, self.autoscript_host_port)
            self.info_stream(f"Connected to AutoScript at {self.autoscript_host_ip}:{self.autoscript_host_port}")
        except Exception as e:
            self.error_stream(f"AutoScript connection failed: {e}")
            self.set_state(DevState.FAULT)
            self._microscope = None

    def _connect_detector_proxies(self) -> None:
        """Build DeviceProxy objects for each configured detector device."""
        # Extend this dict as more detectors are added
        # later, we want to do this automatically, not with a dictionary.
        addresses: dict[str, str] = {
            "eds":  self.eds_device_address,
            "stage": self.stage_device_address,
            "scan": self.scan_device_address,
            "camera": self.camera_device_address,
            "tiled": self.tiled_device_address,
        }
        for name, address in addresses.items():
            if not address:   # <-- minimal fix
                self.info_stream(f"Skipping {name}: no address configured")
                continue
            try:
                self._detector_proxies[name] = tango.DeviceProxy(address)
                self.info_stream(f"Connected to detector proxy: {name} @ {address}")
            except tango.DevFailed as e:
                self.error_stream(f"Failed to connect to {name} proxy at {address}: {e}")


    # ------------------------------------------------------------------
    # Attribute read methods
    # ------------------------------------------------------------------

    def read_manufacturer(self) -> bool:
        # TODO: query self._microscope.optics.mode when AutoScript available
        return self._manufacturer

    def _configure_tiled_acquisition(self, config_json: str) -> str:
        """
        Configure where acquisitions are saved before Tiled serves them.

        Pass JSON with any of: save_directory, file_format.
        Returns the complete active config as JSON.
        """
        incoming = json.loads(config_json) if config_json else {}
        current = self._ensure_tiled_acquisition_config()
        current.update({key: value for key, value in incoming.items() if value is not None})
        self._tiled_acquisition_config = acquisition_config(
            save_directory=current.get("save_directory"),
            file_format=current.get("file_format"),
        )
        return json.dumps(self._tiled_acquisition_config)

    def _get_tiled_acquisition_config(self) -> str:
        """Return the active Tiled acquisition config as JSON."""
        return json.dumps(self._ensure_tiled_acquisition_config())

    # ------------------------------------------------------------------
    # Internal acquisition helpers
    # ------------------------------------------------------------------
    def _acquire_stem_image(self, imsize: int, dwell_time: float, detector_list: list) -> str:
        """
        Call AutoScript acquisition, save the adorned image, and return its path.
        """
        if self._microscope is None:
            self._raise_hardware_unavailable("_acquire_stem_image()")

        detector_type = detector_list[0].upper() if detector_list else "HAADF"
        adorned = self._microscope.acquisition.acquire_stem_image(detector_type, imsize, dwell_time)
        return self._save_adorned_acquisition(
            adorned,
            acquisition_type="stem_image",
            detector=detector_type,
        )

    def _acquire_camera_image(self, imsize: int, exposure_time: float, detector: str, readout_area: str) -> str:
        """
        Call AutoScript acquisition, save the adorned image, and return its path.
        this is the advanced version
        """
        if self._microscope is None:
            self._raise_hardware_unavailable("_acquire_camera_image()")

        settings = CameraAcquisitionSettings(
            camera_detector=detector,
            size=imsize,
            exposure_time=exposure_time,
            fixed_readout_area=readout_area,
            frame_combining=1,
        )
        adorned = self._microscope.acquisition.acquire_camera_image_advanced(settings)
        return self._save_adorned_acquisition(
            adorned,
            acquisition_type="camera_image",
            detector=detector,
        )


    def _acquire_stem_image_advanced(self, imsize: int, dwell_time: float, detector_list: list, scan_region: list[float]) -> list[str]:
        """
        Call AutoScript acquisition, save adorned images, and return their paths.
        """
        if self._microscope is None:
            self._raise_hardware_unavailable("_acquire_stem_image_advanced()")

        detector_list = [d.upper() for d in detector_list]
        detector_type = "HAADF"

        custom_region = Region(
            RegionCoordinateSystem.RELATIVE,
            Rectangle(
                scan_region[0],
                scan_region[1],
                scan_region[2],
                scan_region[3],
            )
        )

        settings = StemAcquisitionSettings(
            dwell_time=dwell_time,
            detector_types=detector_list,
            size=imsize,
            region=custom_region,
        )
        
        adorned = self._microscope.acquisition.acquire_stem_images_advanced(settings)
        adorned_images = adorned if isinstance(adorned, list) else [adorned]
        return [
            self._save_adorned_acquisition(
                image,
                acquisition_type="stem_image",
                detector=detector_type,
            )
            for image in adorned_images
        ]

    def _save_adorned_acquisition(
        self,
        adorned,
        *,
        acquisition_type: str,
        detector: str,
    ) -> str:
        return save_adorned_acquisition(
            adorned,
            acquisition_type=acquisition_type,
            detector=detector,
            config=self._ensure_tiled_acquisition_config(),
        )

    def _ensure_tiled_acquisition_config(self) -> dict[str, str]:
        tiled_proxy = self._detector_proxies.get("tiled")
        if tiled_proxy is not None:
            self._tiled_acquisition_config = acquisition_config(
                save_directory=tiled_proxy.save_path,
                file_format=self.acquisition_file_format,
            )
            return self._tiled_acquisition_config

        self._tiled_acquisition_config = acquisition_config(
            save_directory=getattr(self, "_tiled_acquisition_config", {}).get(
                "save_directory",
                self.acquisition_save_directory,
            ),
            file_format=getattr(self, "_tiled_acquisition_config", {}).get(
                "file_format",
                self.acquisition_file_format,
            ),
        )
        return self._tiled_acquisition_config

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

    def _raise_hardware_unavailable(self, origin: str) -> None:
        tango.Except.throw_exception(
            "HardwareUnavailable",
            "AutoScript microscope hardware is not connected.",
            origin,
        )


    def _acquire_spectrum(self, detector_name: str, exposure_time: float) -> np.ndarray:
        if detector_name.upper() == "EDS":
            # set up settings object
            settings = EdsAcquisitionSettings()
            settings.eds_detector = EdsDetectorType.SUPER_X
            settings.dispersion = 5 # int
            settings.shaping_time = 3e-6 # float
            # TODO: don't hardcode these
            settings.exposure_time = exposure_time
            settings.exposure_time_type = ExposureTimeType.LIVE_TIME

            # take eds
            spectrum = self._microscope.analysis.eds.acquire_spectrum(settings)
            handle_byte_order = True
            if handle_byte_order:
                dt = np.dtype("uint32").newbyteorder("<")
                spectrum = np.frombuffer(spectrum._raw_data, dtype=dt)

        else:
            tango.Except.throw_exception(
                "UnsupportedDetector",
                f"Detector {detector_name} not supported for spectroscopy",
                "_acquire_spectrum()",
            )

        return spectrum


    def _place_beam(self, position) -> None:
        """
        sets resting beam position, [0:1]
        """
        if self._microscope is not None:
            x = float(position[0])
            y = float(position[1])
            self._microscope.optics.paused_scan_beam_position = [x, y]

    def _set_fov(self, fov) -> None:
        """set field of view in meters"""
        self._microscope.optics.scan_field_of_view = fov

    def _get_fov(self) -> float:
        """get field of view in meters"""
        return self._microscope.optics.scan_field_of_view

    def _blank_beam(self) -> None:
        """blank beam"""
        if self._microscope is not None:
            self._microscope.optics.blanker.blank()

    def _unblank_beam(self) -> None:
        """
        unblank beam
        """
        self._microscope.optics.blanker.unblank()

    def _caibrate_screen_current(self) -> None:
        original_gun_lens = self._microscope.optics.monochromator.focus
        gun_lens_series = np.linspace(10, 150, 15)

        # series of measurements
        current_series = []
        for val in gun_lens_series:
            self._microscope.optics.monochromator.focus = val
            time.sleep(1)
            screen_current = self._microscope.detectors.screen.measure_current()
            current_series.append(screen_current)
        current_series = np.array(current_series) * 1e12
        self._microscope.optics.monochromator.focus = original_gun_lens

        # fit a polynomial and save:
        coeffs = np.polyfit(gun_lens_series, current_series, 11)
        poly_func = np.poly1d(coeffs)
        self.screen_current_calibration = poly_func

    def _set_screen_current(self, current) -> None:
        """set screen current in pA"""
        if self.screen_current_calibration is not None:
            poly_func = self.screen_current_calibration
            adjusted_poly = poly_func - current
            x_candidates = adjusted_poly.r
            x_real = x_candidates[np.isreal(x_candidates)].real
            x_real = np.max(x_real) # choose the largest real root as the gun lens value
            self._microscope.optics.monochromator.focus = float(x_real)
        else:
            self.warn_stream("Screen current calibration not available. running calibration (should take 15 seconds).")
            self._caibrate_screen_current()

            poly_func = self.screen_current_calibration
            adjusted_poly = poly_func - current
            x_candidates = adjusted_poly.r
            x_real = x_candidates[np.isreal(x_candidates)].real
            x_real = np.max(x_real) # choose the largest real root as the gun lens value
            self._microscope.optics.monochromator.focus = float(x_real)

    def _get_screen_current(self) -> float:
        """get screen current in pA"""
        screen_current = self._microscope.detectors.screen.measure_current() * 1e12
        return screen_current

    def _get_stage(self):
        """Get the current stage position as a list of floats [x, y, z, alpha, beta]."""
        # set proxy attributes with current stage position
        stage = self._detector_proxies['stage']

        position = self._microscope.specimen.stage.position
        position = np.array(position)

        stage.x = float(position[1])
        stage.y = float(position[0])
        stage.z = float(position[2])
        stage.alpha = float(math.degrees(position[3]))

        if position[4] is not None:
            return position
        else:
            return position[:4]

    def _move_stage(self, position) -> None:
        """Move stage to specified position [x, y, z, alpha, beta]."""
        x = float(position[0])
        y = float(position[1])
        z = float(position[2])
        alpha = float(math.radians(position[3]))

        if len(position) > 4 and position[4] is not None:
            beta = float(math.radians(position[4]))
        else:
            beta = None

        self._microscope.specimen.stage.absolute_move((x, y, z, alpha, beta))
        self._get_stage() # link the proxy with real state

    def _auto_focus(self):
        """Perform autofocus routine C1A1"""
        settings = RunOptiStemSettings(method='C1A1') #method=OptiStemMethod.C1_A1, dwell_time=2e-06, cutoff_in_pixels=5)
        self._microscope.auto_functions.run_opti_stem(settings)

    def _set_image_shift(self, shift):
        """Apply image shift in meters."""
        x_shift = float(shift[0])
        y_shift = float(shift[1])
        try:
            self._microscope.optics.deflectors.beam_shift = (x_shift, y_shift)
        except Exception as e:
            self.error_stream(f"Failed to set beam shift: {e}")

# ----------------------------------------------------------------------
# Server entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    ThermoMicroscope.run_server()
