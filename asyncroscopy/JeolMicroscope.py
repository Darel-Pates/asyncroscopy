## started on 5th June 2026 --> Aimed for JEM-F200

# Import python internal libraries
import math
import time
from datetime import datetime
from pathlib import Path

# Import external libraries
import numpy as np
import tango
from tango import AttrWriteType, DevState
from tango.server import attribute, command, device_property

# Import Asyncroscopy relevant modules
from asyncroscopy.Microscope import Microscope
from asyncroscopy.software.DataWriter import DEFAULT_ACQUISITION_DIR, save_acquisition

# Import Manufacturer-specific libraries



# Define class for Microscope

class JeolMicroscope(Microscope):
    """
    Manages the PyJEM connection and exposes acquisition commands.
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
        default_value="h5",
        doc="Acquisition file format. HDF5 stores acquisition data and parsed metadata attributes.",
    )
    data_device_address = device_property(
        dtype=str,
        default_value="",
        doc="Optional Tango device address for the DATA device, e.g. 'asyncroscopy/data/default'.",
    )
    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Attribute read methods
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Commands pertaining to setting children attributes, e.g. stage position, scan parameters, EDS settings, etc. --> iuser accesses it in a jupyter notebook using the device proxy
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # Internal acquisition helpers
    # ------------------------------------------------------------------
    def _persist(self, adorned, acquisition_type, detector, data_server, dataset_name="image"):
        """Save acquired images in the format requested by the SCAN device.
        """
        scan = self._detector_proxies.get("scan")
        fmt = scan.output_format if scan is not None else ".h5"  # ".h5" default
        if fmt == ".h5":
            return save_acquisition(self, data_server, acquisition_type, detector, adorned, dataset_name=dataset_name)
        if fmt != ".tiff":
            raise ValueError(f"Unsupported output_format {fmt!r}; expected '.h5' or '.tiff'")

        # .tiff → AutoScript native save, one file per detector sharing one stamp
        images = list(adorned) if isinstance(adorned, (list, tuple)) else [adorned]
        detectors = list(detector) if isinstance(detector, (list, tuple)) else [detector]
        if len(images) != len(detectors):
            raise ValueError(f"Got {len(images)} images for {len(detectors)} detector(s) {detectors}")

        save_dir = data_server.save_path if data_server is not None else DEFAULT_ACQUISITION_DIR
        directory = Path(save_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        stem = f"{acquisition_type}_{stamp}"
        # AutoScript returns images in the requested detector order (assumed; verify on hardware)
        for img, det in zip(images, detectors):
            path = directory / f"{stem}_{det}.tiff"
            img.save(str(path))
            if data_server is not None:
                data_server.register_path(str(path))
        return stem

    def _acquire_scanned_image(
        self,
        imsize: int,
        dwell_time: float,
        detector_list: list[str] = ["haadf"],
        scan_region: list[float] = [0.0, 0.0, 1.0, 1.0],
    ) -> str:
        """
        Call AutoScript scanned image acquisition, save one HDF5 file, and return its DATA/Tiled key.
        """
        detector_list = [d.upper() for d in detector_list]
        #settings = StemAcquisitionSettings(dwell_time=dwell_time, detector_types=detector_list, size=imsize, region=Region(RegionCoordinateSystem.RELATIVE, Rectangle(*scan_region)))
        #adorned = self._microscope.acquisition.acquire_stem_images_advanced(settings)
        if not isinstance(adorned, list):
            adorned = [adorned]
        data_server = self._detector_proxies.get("data")
        return self._persist(adorned, "stem_image", detector_list, data_server)

