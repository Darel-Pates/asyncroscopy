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
DATA/Tiled unique id for that saved acquisition.
"""

import math
import time
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np
import tango
from tango import AttrWriteType, DevState
from tango.server import attribute, device_property

from asyncroscopy.Microscope import Microscope

DEFAULT_ACQUISITION_DIR = "outputs/tiled_acquisitions"

# AutoScript imports — only available on the microscope PC.
# Wrapped in try/except so the device can still be imported and tested
# on a development machine without AutoScript installed.
try:
    from autoscript_tem_microscope_client import TemMicroscopeClient
    from autoscript_tem_microscope_client.enumerations import EdsDetectorType
    from autoscript_tem_microscope_client.enumerations import CameraType, RegionCoordinateSystem, ExposureTimeType
    from autoscript_tem_microscope_client.structures import Region, Rectangle
    from autoscript_tem_microscope_client.structures import StemAcquisitionSettings, EdsAcquisitionSettings, RunOptiStemSettings, CameraAcquisitionSettings, StemDataSettings

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
        doc="Directory where AutoScript acquisitions are saved before the Tiled server serves them.",
    )
    acquisition_file_format = device_property(
        dtype=str,
        default_value="tiff",
        doc="Acquisition file format. TIFF preserves AutoScript image metadata.",
    )
    data_device_address = device_property(
        dtype=str,
        default_value="",
        doc="Optional Tango device address for the DATA device, e.g. 'asyncroscopy/data/default'.",
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
            self.is_autoscript = True
        except Exception as e:
            self.error_stream(f"AutoScript connection failed: {e}")
            self.set_state(DevState.FAULT)
            self._microscope = None
            self.is_autoscript = False

    def _connect_detector_proxies(self) -> None:
        """Build DeviceProxy objects for each configured detector device."""
        # Extend this dict as more detectors are added
        # later, we want to do this automatically, not with a dictionary.
        addresses: dict[str, str] = {
            "eds": self.eds_device_address,
            "stage": self.stage_device_address,
            "scan": self.scan_device_address,
            "camera": self.camera_device_address,
            "flucam": self.flucam_device_address,
            "data": self.data_device_address,
        }
        for name, address in addresses.items():
            if not address:  # <-- minimal fix
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

    # ------------------------------------------------------------------
    # Internal acquisition helpers
    # ------------------------------------------------------------------
    def _acquire_stem_image(self, imsize: int, dwell_time: float, detector_list: list) -> str:
        """
        Call AutoScript acquisition, save the adorned image, and return its path.
        """
        detector_type = detector_list[0].upper() if detector_list else "HAADF"
        adorned = self._microscope.acquisition.acquire_stem_image(detector_type, imsize, dwell_time)
        data_server = self._detector_proxies.get("data")
        path = self._new_acquisition_path("stem_image", detector_type, data_server)
        adorned.save(str(path))
        return data_server.register_path(str(path))

    def _acquire_camera_image(self, imsize: int, exposure_time: float, detector: str, readout_area: str) -> str:
        """
        Call AutoScript acquisition, save the adorned image, and return its path.
        this is the advanced version
        """
        settings = CameraAcquisitionSettings(camera_detector=detector, size=imsize, exposure_time=exposure_time, fixed_readout_area=readout_area, frame_combining=1)
        adorned = self._microscope.acquisition.acquire_camera_image_advanced(settings)
        data_server = self._detector_proxies.get("data")
        path = self._new_acquisition_path("camera_image", detector, data_server)
        adorned.save(str(path))
        return data_server.register_path(str(path))

    def _acquire_stem_image_advanced(
        self,
        imsize: int,
        dwell_time: float,
        detector_list: list,
        scan_region: list[float],
    ) -> list[str]:
        """
        Call AutoScript acquisition, save adorned images, and return their paths.
        """
        detector_list = [d.upper() for d in detector_list]

        settings = StemAcquisitionSettings(dwell_time=dwell_time, detector_types=detector_list, size=imsize, region=Region(RegionCoordinateSystem.RELATIVE, Rectangle(*scan_region)))

        adorned = self._microscope.acquisition.acquire_stem_images_advanced(settings)
        adorned_images = adorned if isinstance(adorned, list) else [adorned]
        saved_paths = []
        data_server = self._detector_proxies.get("data")
        for image, detector in zip(adorned_images, detector_list):
            path = self._new_acquisition_path("stem_image", detector, data_server)
            image.save(str(path))
            saved_paths.append(data_server.register_path(str(path)))
        return saved_paths

    def _acquire_stem_data_advanced(
        self,
        imsize: int,
        dwell_time: float,
        detector: str,
        scan_region: list[float],
    ) -> str:
        """
        Trigger AutoScript advanced STEM data acquisition with a camera detector.

        AutoScript offloads the 4D STEM data storage for Ceta acquisitions, so
        this command returns an acknowledgement and the settings used rather
        than a local saved file path.
        """
        camera_detector = CameraType.BM_CETA if detector == "BM-Ceta" else detector
        settings = StemDataSettings(dwell_time=dwell_time, detector_types=[camera_detector], size=imsize, region=Region(RegionCoordinateSystem.RELATIVE, Rectangle(*scan_region)))
        adorned = self._microscope.acquisition.acquire_stem_data_advanced(settings)
        data_server = self._detector_proxies.get("data")
        path = self._new_acquisition_path("stem_data", detector, data_server)
        adorned.save(str(path))
        return data_server.register_path(str(path))

    def _new_acquisition_path(self, acquisition_type: str, detector: str, data_server, extension: str = "tiff") -> Path:
        save_directory = self.acquisition_save_directory
        if data_server is not None:
            try:
                save_directory = data_server.save_path
            except tango.DevFailed as exc:
                self.warn_stream(f"DATA device not ready: {exc}")

        directory = Path(save_directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        name = f"{acquisition_type}_{detector}_{stamp}.{extension.lower().lstrip('.')}"
        return directory / name

    # test: not sure this is how we want to save
    def _acquire_spectrum(self, detector_name: str, exposure_time: float) -> str:
        settings = EdsAcquisitionSettings()
        settings.eds_detector = EdsDetectorType.SUPER_X
        settings.dispersion = 5
        settings.shaping_time = 3e-6
        settings.exposure_time = exposure_time
        settings.exposure_time_type = ExposureTimeType.LIVE_TIME
        spectrum = self._microscope.analysis.eds.acquire_spectrum(settings)
        data_server = self._detector_proxies.get("data")
        path = self._new_acquisition_path("spectrum", detector_name, data_server, "emd")
        with h5py.File(path, "w") as emd:
            emd.create_dataset("spectrum", data=spectrum.data)
        return data_server.register_path(str(path))

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
            x_real = np.max(x_real)  # choose the largest real root as the gun lens value
            self._microscope.optics.monochromator.focus = float(x_real)
        else:
            self.warn_stream("Screen current calibration not available. running calibration (should take 15 seconds).")
            self._caibrate_screen_current()

            poly_func = self.screen_current_calibration
            adjusted_poly = poly_func - current
            x_candidates = adjusted_poly.r
            x_real = x_candidates[np.isreal(x_candidates)].real
            x_real = np.max(x_real)  # choose the largest real root as the gun lens value
            self._microscope.optics.monochromator.focus = float(x_real)

    def _get_screen_current(self) -> float:
        """get screen current in pA"""
        screen_current = self._microscope.detectors.screen.measure_current() * 1e12
        return screen_current

    def _get_stage(self):
        """Get the current stage position as a list of floats [x, y, z, alpha, beta]."""
        # set proxy attributes with current stage position
        stage = self._detector_proxies["stage"]

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
        self._get_stage()  # link the proxy with real state

    def _auto_focus(self):
        """Perform autofocus routine C1A1"""
        settings = RunOptiStemSettings(method="C1A1")  # method=OptiStemMethod.C1_A1, dwell_time=2e-06, cutoff_in_pixels=5)
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
