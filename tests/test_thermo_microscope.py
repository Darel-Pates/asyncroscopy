import json
from pathlib import Path

import pytest
import tango


class TestThermoMicroscope:
    def test_startup_state_is_on(self, thermo_proxy: tango.DeviceProxy) -> None:
        assert thermo_proxy.state() == tango.DevState.ON

    def test_scan_defaults_are_visible_through_proxy(self, scan_proxy: tango.DeviceProxy) -> None:
        scan_proxy.dwell_time = 1e-6
        scan_proxy.imsize = 512
        assert scan_proxy.state() == tango.DevState.ON
        assert scan_proxy.dwell_time == pytest.approx(1e-6)
        assert scan_proxy.imsize == 512

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

    def test_tiled_acquisition_config_uses_tiled_device_save_path(
        self,
        thermo_proxy: tango.DeviceProxy,
        tiled_proxy: tango.DeviceProxy,
        tiled_save_dir,
    ) -> None:
        tiled_proxy.save_path = str(tiled_save_dir)

        config = json.loads(thermo_proxy.get_tiled_acquisition_config())

        assert config["save_directory"] == str(tiled_save_dir)
        assert config["file_format"] == "tiff"

    def test_unknown_detector_raises(self, thermo_proxy: tango.DeviceProxy) -> None:
        with pytest.raises(tango.DevFailed) as exc:
            thermo_proxy.get_spectrum("void")

        err_text = str(exc.value)

        assert "UnknownDetector" in err_text
        assert "void" in err_text

    def test_disconnect_sets_state_off(self, thermo_proxy: tango.DeviceProxy) -> None:
        thermo_proxy.Disconnect()
        assert thermo_proxy.state() == tango.DevState.OFF

    def test_connect_restores_state_on(self, thermo_proxy: tango.DeviceProxy) -> None:
        thermo_proxy.Disconnect()
        assert thermo_proxy.state() == tango.DevState.OFF

        thermo_proxy.Connect()
        assert thermo_proxy.state() == tango.DevState.ON
