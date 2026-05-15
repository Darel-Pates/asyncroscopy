"""
Shared pytest fixtures for Tango device tests.

Starts BOTH the detector device(s) and the Microscope device in ONE Tango
test device server using MultiDeviceTestContext, so the Microscope can
create DeviceProxy connections to detectors by device name.

This avoids:
- "No proxy found for detector 'scan'. Available: []"
- Needing a real Tango DB
- Flaky multi-context issues from spinning up multiple separate servers
"""

import numpy as np
import pytest
import tango
from tango.test_context import MultiDeviceTestContext

# Import device classes to test
from asyncroscopy.detectors.CAMERA import CAMERA
from asyncroscopy.detectors.EDS import EDS
from asyncroscopy.detectors.FLUCAM import FLUCAM
from asyncroscopy.hardware.SCAN import SCAN
from asyncroscopy.hardware.STAGE import STAGE
from asyncroscopy.ThermoDigitalTwin import ThermoDigitalTwin
from asyncroscopy.ThermoMicroscope import ThermoMicroscope
from asyncroscopy.Tiled import Tiled


class FakeAdornedImage:
    def __init__(self, data: np.ndarray):
        self.data = data


# We use ThermoDigitalTwin as our simulated microscope for all tests.
    
@pytest.fixture(scope="session")
def tiled_save_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("tiled-acquisitions")


@pytest.fixture(scope="session")
def tango_ctx(tiled_save_dir):
    """
    One Tango device server hosting SCAN + Microscope together.

    Device names here MUST match what you put into Microscope properties.
    """
    devices_info = [
        {
            "class": SCAN,
            "devices": [
                {
                    "name": "asyncroscopy/scan/default",
                    "properties": {
                        # put SCAN defaults here if you want
                        # e.g. "dwell_time": 2e-6  (only if it's a device_property)
                    },
                }
            ],
        },
        {
            "class": EDS,
            "devices": [
                {
                    "name": "asyncroscopy/eds/default",
                    "properties": {},
                }
            ],
        },
        {
            "class": CAMERA,
            "devices": [
                {
                    "name": "asyncroscopy/camera/default",
                    "properties": {},
                }
            ],
        },
        {
            "class": FLUCAM,
            "devices": [
                {
                    "name": "asyncroscopy/flucam/default",
                    "properties": {},
                }
            ],
        },
        {
            "class": STAGE,
            "devices": [
                {
                    "name": "asyncroscopy/stage/default",
                    "properties": {},
                }
            ],
        },
        {
            "class": Tiled,
            "devices": [
                {
                    "name": "asyncroscopy/tiled/default",
                    "properties": {},
                }
            ],
        },
        {
            "class": ThermoDigitalTwin,
            "devices": [
                {
                    "name": "asyncroscopy/digitaltwin/default",
                    "properties": {
                        "scan_device_address": "asyncroscopy/scan/default",
                        "eds_device_address": "asyncroscopy/eds/default",
                        "stage_device_address": "asyncroscopy/stage/default",
                        "camera_device_address": "asyncroscopy/camera/default",
                        "flucam_device_address": "asyncroscopy/flucam/default",
                    },
                }
            ],
        },

        {
            "class": ThermoMicroscope,
            "devices": [
                {
                    "name": "asyncroscopy/thermomicroscope/default",
                    "properties": {
                        "testing_mode_bool": True,
                        "scan_device_address": "asyncroscopy/scan/default",
                        "camera_device_address": "asyncroscopy/camera/default",
                        "flucam_device_address": "asyncroscopy/flucam/default",
                        "eds_device_address": "asyncroscopy/eds/default",
                        "stage_device_address": "asyncroscopy/stage/default",
                        "tiled_device_address": "asyncroscopy/tiled/default",
                    },
                }
            ],
        },
    ]

    # Keep one in-process context for the whole session. Starting multiple
    # in-process Tango contexts in one interpreter can segfault in PyTango.
    ctx = MultiDeviceTestContext(devices_info, process=False)
    with ctx:
        tiled = tango.DeviceProxy(ctx.get_device_access("asyncroscopy/tiled/default"))
        tiled.save_path = str(tiled_save_dir)
        yield ctx



@pytest.fixture(scope="session")
def scan_proxy(tango_ctx):
    return tango.DeviceProxy(tango_ctx.get_device_access("asyncroscopy/scan/default"))


@pytest.fixture(scope="session")
def twin_proxy(tango_ctx):
    return tango.DeviceProxy(tango_ctx.get_device_access("asyncroscopy/digitaltwin/default"))


@pytest.fixture(scope="session")
def eds_proxy(tango_ctx):
    return tango.DeviceProxy(tango_ctx.get_device_access("asyncroscopy/eds/default"))


@pytest.fixture(scope="session")
def camera_proxy(tango_ctx):
    return tango.DeviceProxy(tango_ctx.get_device_access("asyncroscopy/camera/default"))


@pytest.fixture(scope="session")
def flucam_proxy(tango_ctx):
    return tango.DeviceProxy(tango_ctx.get_device_access("asyncroscopy/flucam/default"))


@pytest.fixture(scope="session")
def stage_proxy(tango_ctx):
    return tango.DeviceProxy(tango_ctx.get_device_access("asyncroscopy/stage/default"))


@pytest.fixture(scope="session")
def tiled_proxy(tango_ctx):
    return tango.DeviceProxy(tango_ctx.get_device_access("asyncroscopy/tiled/default"))


@pytest.fixture(scope="session")
def thermo_proxy(tango_ctx):
    return tango.DeviceProxy(tango_ctx.get_device_access("asyncroscopy/thermomicroscope/default"))



@pytest.fixture
def patched_single_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Patch ThermoMicroscope._acquire_stem_image so get_image() works
    without AutoScript/hardware.
    """
    def fake_acquire(self, imsize: int, dwell_time: float, detector_list: list):
        # Deterministic image makes tests stable
        arr = np.arange(imsize * imsize, dtype=np.uint16)
        return FakeAdornedImage(arr.reshape(imsize, imsize))

    monkeypatch.setattr(
        ThermoMicroscope,
        "_acquire_stem_image",
        fake_acquire,
    )
    monkeypatch.setattr(
        ThermoDigitalTwin,
        "_acquire_stem_image",
        fake_acquire,
    )


@pytest.fixture
def patched_path_acquisition(monkeypatch: pytest.MonkeyPatch, tmp_path):
    calls = []

    def fake_acquire(self, imsize: int, dwell_time: float, detector_list: list):
        calls.append(
            {
                "imsize": imsize,
                "dwell_time": dwell_time,
                "detector_list": list(detector_list),
            }
        )
        path = tmp_path / f"stem_{imsize}.tiff"
        path.write_bytes(b"fake-tiff")
        return str(path)

    monkeypatch.setattr(ThermoMicroscope, "_acquire_stem_image", fake_acquire)
    return calls


@pytest.fixture
def patched_camera_path_acquisition(monkeypatch: pytest.MonkeyPatch, tmp_path):
    calls = []

    def fake_acquire(self, imsize: int, exposure_time: float, detector: str, readout_area: str):
        calls.append(
            {
                "imsize": imsize,
                "exposure_time": exposure_time,
                "detector": detector,
                "readout_area": readout_area,
            }
        )
        path = tmp_path / f"camera_{imsize}.tiff"
        path.write_bytes(b"fake-camera-tiff")
        return str(path)

    monkeypatch.setattr(ThermoMicroscope, "_acquire_camera_image", fake_acquire)
    return calls
