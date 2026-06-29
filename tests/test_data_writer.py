import types

import h5py
import numpy as np

from asyncroscopy.data.data_writer import save_acquisition, save_acquisition_hdf5


class FakeDataServer:
    def __init__(self, save_path):
        self.save_path = str(save_path)

    def register_path(self, path: str) -> str:
        return path


def test_save_acquisition_hdf5_writes_data_and_xml_leaf_attrs(tmp_path):
    source = types.SimpleNamespace(
        data=np.array([[1, 2], [3, 4]], dtype=np.uint16),
        metadata=types.SimpleNamespace(
            metadata_as_xml="<root><detector>HAADF</detector><empty /></root>",
        ),
    )
    path = tmp_path / "acquisition.h5"

    save_acquisition_hdf5(
        path,
        [
            {
                "name": "images/HAADF",
                "source": source,
                "attrs": {"acquisition_type": "stem_image"},
            }
        ],
    )

    with h5py.File(path, "r") as h5:
        assert h5["images/HAADF"][()].tolist() == [[1, 2], [3, 4]]
        assert h5["images/HAADF"].attrs["acquisition_type"] == "stem_image"
        assert h5["images/HAADF"].attrs["detector"] == "HAADF"


def test_save_acquisition_writes_scanned_images_as_ordered_image_detector_datasets(tmp_path):
    data_server = FakeDataServer(tmp_path)
    detectors = ["HAADF", "BF-S", "DF-S"]
    images = [np.full((2, 2), index, dtype=np.uint8) for index in range(len(detectors))]

    path = save_acquisition(object(), data_server, "stem_image", detectors, images)

    with h5py.File(path, "r") as h5:
        assert list(h5.keys()) == ["image"]
        assert list(h5["image"].keys()) == detectors
        for index, detector in enumerate(detectors):
            assert h5[f"image/{detector}"][()].tolist() == images[index].tolist()
            assert h5[f"image/{detector}"].attrs["detector"] == detector
