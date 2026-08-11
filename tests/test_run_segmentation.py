from pathlib import Path

import pytest

from startup_scripts import run_segmentation


class FakeDatabase:
    def __init__(self) -> None:
        self.device = None
        self.properties = None

    def add_device(self, device) -> None:
        self.device = device

    def put_device_property(self, name: str, properties: dict) -> None:
        self.properties = (name, properties)


def test_load_config() -> None:
    config = run_segmentation.load_config(
        run_segmentation.PROJECT_DIR / "configs" / "Segmentation.yaml"
    )

    assert config.tango == run_segmentation.TangoConfig("localhost", 9094)
    assert config.data_device_address == "asyncroscopy/data/default"
    assert config.model_size == "facebook/sam2-hiera-large"


def test_register_device(monkeypatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr(
        run_segmentation.tango,
        "Database",
        lambda host, port: database,
    )

    class FakeDeviceInfo:
        pass

    monkeypatch.setattr(run_segmentation.tango, "DbDevInfo", FakeDeviceInfo)
    config = run_segmentation.SegmentationConfig(
        tango=run_segmentation.TangoConfig("localhost", 9094)
    )

    run_segmentation.register_device(config)

    assert database.device.server == "SEGMENTATION/segment_instance"
    assert database.device._class == "SEGMENTATION"
    assert database.device.name == "asyncroscopy/segment/default"
    assert database.properties == (
        "asyncroscopy/segment/default",
        {
            "data_device_address": ["asyncroscopy/data/default"],
            "model_size": ["facebook/sam2-hiera-large"],
        },
    )


def test_missing_config(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_segmentation.load_config(tmp_path / "missing.yaml")
