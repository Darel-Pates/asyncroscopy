import types
from pathlib import Path

import h5py
import numpy as np
import pytest
import tango
from autoscript_tem_microscope_client.enumerations import (
    CameraType,
    EdsDetectorType,
    ExposureTimeType,
    RegionCoordinateSystem,
)

from asyncroscopy.ThermoMicroscope import ThermoMicroscope


class FakeDataServer:
    def register_path(self, path: str) -> str:
        return path


class TestThermoMicroscope:
    def test_startup_state_is_on(self, thermo_proxy: tango.DeviceProxy) -> None:
        assert thermo_proxy.state() == tango.DevState.ON

    def test_scan_defaults_are_visible_through_proxy(self, scan_proxy: tango.DeviceProxy) -> None:
        scan_proxy.dwell_time = 1e-6
        scan_proxy.imsize = 512
        scan_proxy.scan_region = [0.0, 0.0, 1.0, 1.0]
        assert scan_proxy.state() == tango.DevState.ON
        assert scan_proxy.dwell_time == pytest.approx(1e-6)
        assert scan_proxy.imsize == 512
        assert list(scan_proxy.scan_region) == [0.0, 0.0, 1.0, 1.0]

    def test_get_scanned_image_returns_saved_path(
        self,
        thermo_proxy: tango.DeviceProxy,
        scan_proxy: tango.DeviceProxy,
        patched_path_acquisition: list[dict],
    ) -> None:
        scan_proxy.dwell_time = 1e-6
        scan_proxy.imsize = 512

        saved_path = thermo_proxy.get_scanned_image()

        assert isinstance(saved_path, str)
        assert saved_path.endswith(".tiff")
        assert Path(saved_path).read_bytes() == b"fake-tiff"
        assert patched_path_acquisition == [
            {
                "imsize": 512,
                "dwell_time": pytest.approx(1e-6),
                "detector_list": ["haadf"],
            }
        ]

    def test_scan_settings_propagate_into_acquisition(
        self,
        thermo_proxy: tango.DeviceProxy,
        scan_proxy: tango.DeviceProxy,
        patched_path_acquisition: list[dict],
    ) -> None:
        scan_proxy.dwell_time = 2e-6
        scan_proxy.imsize = 256

        saved_path = thermo_proxy.get_scanned_image()

        assert Path(saved_path).exists()
        assert patched_path_acquisition[-1] == {
            "imsize": 256,
            "dwell_time": pytest.approx(2e-6),
            "detector_list": ["haadf"],
        }

    def test_advanced_scan_settings_propagate_into_acquisition(
        self,
        thermo_proxy: tango.DeviceProxy,
        scan_proxy: tango.DeviceProxy,
        patched_advanced_path_acquisition: list[dict],
    ) -> None:
        scan_proxy.dwell_time = 3e-6
        scan_proxy.imsize = 128
        scan_proxy.scan_region = [0.1, 0.2, 0.3, 0.4]
        scan_proxy.haadf = True
        scan_proxy.bf = False

        saved_path = thermo_proxy.get_scanned_image_advanced()

        assert Path(saved_path).read_bytes() == b"fake-advanced-tiff"
        assert patched_advanced_path_acquisition == [
            {
                "imsize": 128,
                "dwell_time": pytest.approx(3e-6),
                "detector_list": ["haadf"],
                "scan_region": [0.1, 0.2, 0.3, 0.4],
            }
        ]

    def test_advanced_stem_image_helper_uses_relative_region(self, tmp_path) -> None:
        class FakeImage:
            def save(self, path: str) -> None:
                Path(path).write_bytes(b"fake")

        class FakeAcquisition:
            def __init__(self) -> None:
                self.settings = None

            def acquire_stem_images_advanced(self, settings):
                self.settings = settings
                return [FakeImage()]

        acquisition = FakeAcquisition()
        microscope = ThermoMicroscope.__new__(ThermoMicroscope)
        microscope._microscope = types.SimpleNamespace(acquisition=acquisition)
        microscope._detector_proxies = {"data": FakeDataServer()}

        def fake_new_path(self, acquisition_type: str, detector: str, data_server):
            return tmp_path / f"{acquisition_type}_{detector}.tiff"

        microscope._new_acquisition_path = types.MethodType(fake_new_path, microscope)

        saved_paths = ThermoMicroscope._acquire_stem_image_advanced(
            microscope,
            imsize=128,
            dwell_time=4e-6,
            detector_list=["haadf"],
            scan_region=[0.1, 0.2, 0.3, 0.4],
        )

        settings = acquisition.settings
        assert saved_paths[0].endswith(".tiff")
        assert settings.size == 128
        assert settings.dwell_time == pytest.approx(4e-6)
        assert settings.detector_types == ["HAADF"]
        assert settings.region.coordinate_system == RegionCoordinateSystem.RELATIVE
        assert settings.region.rectangle.left == pytest.approx(0.1)
        assert settings.region.rectangle.top == pytest.approx(0.2)
        assert settings.region.rectangle.width == pytest.approx(0.3)
        assert settings.region.rectangle.height == pytest.approx(0.4)

    def test_scanned_data_advanced_settings_propagate_into_acquisition(
        self,
        thermo_proxy: tango.DeviceProxy,
        scan_proxy: tango.DeviceProxy,
        patched_stem_data_acquisition: list[dict],
    ) -> None:
        scan_proxy.dwell_time = 10e-3
        scan_proxy.imsize = 128
        scan_proxy.scan_region = [0.0, 0.0, 0.5, 0.5]

        result = thermo_proxy.get_scanned_data_advanced()

        assert result == "fake-stem-data-key"
        assert patched_stem_data_acquisition == [
            {
                "imsize": 128,
                "dwell_time": pytest.approx(10e-3),
                "detector": "BM-Ceta",
                "scan_region": [0.0, 0.0, 0.5, 0.5],
            }
        ]

    def test_stem_data_advanced_helper_saves_and_registers_ceta_with_relative_region(self, tmp_path) -> None:
        class FakeImage:
            def save(self, path: str) -> None:
                Path(path).write_bytes(b"fake-stem-data")

        class FakeAcquisition:
            def __init__(self) -> None:
                self.settings = None

            def acquire_stem_data_advanced(self, settings):
                self.settings = settings
                return FakeImage()

        acquisition = FakeAcquisition()
        microscope = ThermoMicroscope.__new__(ThermoMicroscope)
        microscope._microscope = types.SimpleNamespace(acquisition=acquisition)
        microscope._detector_proxies = {"data": FakeDataServer()}
        microscope._new_acquisition_path = types.MethodType(lambda self, acquisition_type, detector, data_server: tmp_path / f"{acquisition_type}_{detector}.tiff", microscope)

        result = ThermoMicroscope._acquire_stem_data_advanced(
            microscope,
            imsize=128,
            dwell_time=10e-3,
            detector="BM-Ceta",
            scan_region=[0.25, 0.25, 0.5, 0.5],
        )

        settings = acquisition.settings
        assert Path(result).read_bytes() == b"fake-stem-data"
        assert settings.size == 128
        assert settings.dwell_time == pytest.approx(10e-3)
        assert settings.detector_types == [CameraType.BM_CETA]
        assert settings.region.coordinate_system == RegionCoordinateSystem.RELATIVE
        assert settings.region.rectangle.left == pytest.approx(0.25)
        assert settings.region.rectangle.top == pytest.approx(0.25)
        assert settings.region.rectangle.width == pytest.approx(0.5)
        assert settings.region.rectangle.height == pytest.approx(0.5)

    def test_camera_settings_propagate_into_acquisition(
        self,
        thermo_proxy: tango.DeviceProxy,
        camera_proxy: tango.DeviceProxy,
        patched_camera_path_acquisition: list[dict],
    ) -> None:
        camera_proxy.exposure_time = 0.25
        camera_proxy.imsize = 2048
        camera_proxy.readout_area = "Half"

        saved_path = thermo_proxy.get_camera_image()

        assert Path(saved_path).read_bytes() == b"fake-camera-tiff"
        assert patched_camera_path_acquisition == [
            {
                "imsize": 2048,
                "exposure_time": pytest.approx(0.25),
                "detector": "BM-Ceta",
                "readout_area": "Half",
            }
        ]

    def test_flucam_settings_propagate_into_acquisition(
        self,
        thermo_proxy: tango.DeviceProxy,
        flucam_proxy: tango.DeviceProxy,
        patched_camera_path_acquisition: list[dict],
    ) -> None:
        flucam_proxy.exposure_time = 0.5
        flucam_proxy.imsize = 1024
        flucam_proxy.readout_area = "Full"

        saved_path = thermo_proxy.get_flucam_image()

        assert Path(saved_path).read_bytes() == b"fake-camera-tiff"
        assert patched_camera_path_acquisition == [
            {
                "imsize": 1024,
                "exposure_time": pytest.approx(0.5),
                "detector": "Flucam",
                "readout_area": "Full",
            }
        ]

    def test_spectrum_settings_propagate_into_acquisition(
        self,
        thermo_proxy: tango.DeviceProxy,
        eds_proxy: tango.DeviceProxy,
        patched_spectrum_path_acquisition: list[dict],
    ) -> None:
        eds_proxy.exposure_time = 0.25

        saved_path = thermo_proxy.get_spectrum("eds")

        assert Path(saved_path).read_bytes() == b"fake-emd"
        assert patched_spectrum_path_acquisition == [{"detector_name": "eds", "exposure_time": pytest.approx(0.25)}]

    def test_spectrum_helper_saves_emd_and_registers(self, tmp_path) -> None:
        class FakeSpectrum:
            data = np.array([1, 2, 3], dtype=np.uint32)

        class FakeEds:
            def __init__(self) -> None:
                self.settings = None

            def acquire_spectrum(self, settings):
                self.settings = settings
                return FakeSpectrum()

        eds = FakeEds()
        microscope = ThermoMicroscope.__new__(ThermoMicroscope)
        microscope._microscope = types.SimpleNamespace(analysis=types.SimpleNamespace(eds=eds))
        microscope._detector_proxies = {"data": FakeDataServer()}
        microscope._new_acquisition_path = types.MethodType(lambda self, acquisition_type, detector, data_server, extension="tiff": tmp_path / f"{acquisition_type}_{detector}.{extension}", microscope)

        result = ThermoMicroscope._acquire_spectrum(microscope, "eds", 0.25)

        assert result.endswith(".emd")
        with h5py.File(result, "r") as emd:
            assert emd["spectrum"][()].tolist() == [1, 2, 3]
        assert eds.settings.eds_detector == EdsDetectorType.SUPER_X
        assert eds.settings.exposure_time == pytest.approx(0.25)
        assert eds.settings.exposure_time_type == ExposureTimeType.LIVE_TIME

    def test_disconnect_sets_state_off(self, thermo_proxy: tango.DeviceProxy) -> None:
        thermo_proxy.Disconnect()
        assert thermo_proxy.state() == tango.DevState.OFF

    def test_connect_restores_state_on(self, thermo_proxy: tango.DeviceProxy) -> None:
        thermo_proxy.Disconnect()
        assert thermo_proxy.state() == tango.DevState.OFF

        thermo_proxy.Connect()
        assert thermo_proxy.state() == tango.DevState.ON
