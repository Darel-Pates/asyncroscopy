from pathlib import Path

import h5py
import numpy as np

from asyncroscopy.instruments.electron_microscope.hackathon_digital_twin import HackathonDigitalTwin


def test_hackathon_digital_twin_reads_camera_slice_from_beam_position(tmp_path: Path):
    source_path = tmp_path / 'camera_source.h5'
    stack = np.arange(4 * 5 * 3 * 2, dtype=np.uint16).reshape(4, 5, 3, 2)
    with h5py.File(source_path, 'w') as h5:
        h5.create_dataset('source/camera_stack', data=stack)

    twin = HackathonDigitalTwin.__new__(HackathonDigitalTwin)
    twin._tango_properties = {}
    twin._beam_pos_x = 1.0
    twin._beam_pos_y = 0.0
    twin.camera_source_path = str(source_path)
    twin.camera_source_dataset = 'source/camera_stack'

    frame, metadata = twin._camera_source_frame()

    assert frame.tolist() == stack[3, 0].tolist()
    assert metadata['beam_index'] == [3, 0]
    assert metadata['source_slice'] == '[3, 0, :, :]'
