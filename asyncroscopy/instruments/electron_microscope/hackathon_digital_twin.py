"""Hackathon digital twin backed by a 4D camera dataset."""

from __future__ import annotations

import os
from pathlib import Path

import h5py
import numpy as np
import tango
from tango.server import device_property

from asyncroscopy.data.data_writer import acquisition_filename, save_acquisition_hdf5
from asyncroscopy.instruments.electron_microscope.digital_twin import DigitalTwin

DEFAULT_CAMERA_SOURCE_PATH = '/Users/austin/Downloads/18167694/hackathon_data/hackathon_camera_source.h5'


class HackathonDigitalTwin(DigitalTwin):
    """Digital twin that returns camera frames from a precomputed 4D-STEM HDF5 source."""

    camera_source_path = device_property(
        dtype=str,
        default_value=DEFAULT_CAMERA_SOURCE_PATH,
        doc='HDF5 file containing a 4D camera stack indexed as beam_x, beam_y, camera_y, camera_x.',
    )
    camera_source_dataset = device_property(
        dtype=str,
        default_value='source/camera_stack',
        doc='Dataset path inside camera_source_path used by acquire_camera_image.',
    )

    def _connect_detector_proxies(self) -> None:
        addresses: dict[str, str] = {
            'eds': self.eds_device_address,
            'camera': self.camera_device_address,
            'flucam': self.flucam_device_address,
            'stage': self.stage_device_address,
            'scan': self.scan_device_address,
            'corrector': self.corrector_device_address,
            'data': self.data_device_address,
        }
        for name, address in addresses.items():
            if not address:
                self.info_stream(f'Skipping {name}: no address configured')
                continue
            try:
                proxy = tango.DeviceProxy(address)
                proxy.set_timeout_millis(12_000)
                self._detector_proxies[name] = proxy
                self.info_stream(f'Connected to detector proxy: {name} @ {address}')
            except tango.DevFailed as exc:
                self.error_stream(f'Failed to connect to {name} proxy at {address}: {exc}')

    def _camera_source_frame(self) -> tuple[np.ndarray, dict]:
        source_path = str(self.camera_source_path).strip() or os.environ.get('ASYNCROSCOPY_HACKATHON_CAMERA_SOURCE_PATH', '').strip()
        if not source_path:
            tango.Except.throw_exception(
                'NoCameraSource',
                'Set camera_source_path to an HDF5 file with a 4D camera stack before calling acquire_camera_image().',
                '_acquire_camera_image()',
            )

        path = Path(source_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f'camera_source_path does not exist: {path}')

        dataset_path = str(self.camera_source_dataset).strip() or 'source/camera_stack'
        beam_x, beam_y = self.read_beam_pos()
        with h5py.File(path, 'r') as h5:
            if dataset_path not in h5:
                raise KeyError(f'{dataset_path!r} not found in {path}')
            source = h5[dataset_path]
            if source.ndim != 4:
                raise ValueError(f'{dataset_path!r} must be 4D, got shape {source.shape}')

            ix = int(np.clip(round(float(beam_x) * (source.shape[0] - 1)), 0, source.shape[0] - 1))
            iy = int(np.clip(round(float(beam_y) * (source.shape[1] - 1)), 0, source.shape[1] - 1))
            frame = np.asarray(source[ix, iy, :, :])
            metadata = {
                'beam_position_fractional': [float(beam_x), float(beam_y)],
                'beam_index': [ix, iy],
                'source_file': str(path),
                'source_dataset': dataset_path,
                'source_shape': list(source.shape),
                'source_slice': f'[{ix}, {iy}, :, :]',
            }
        return frame, metadata

    def _acquire_camera_image(self, imsize: int, exposure_time: float, detector: str, readout_area: str) -> str:
        frame, metadata = self._camera_source_frame()
        data_server = self._detector_proxies.get('data')
        path = acquisition_filename(self, 'camera_image', str(detector), data_server)
        metadata.update(
            {
                'acquisition_type': 'camera_image',
                'detector': str(detector),
                'requested_imsize': int(imsize),
                'exposure_time': float(exposure_time),
                'readout_area': str(readout_area),
            }
        )
        save_acquisition_hdf5(path, [{'name': 'image', 'source': frame, 'attrs': metadata}])
        return data_server.register_path(str(path)) if data_server is not None else str(path)


if __name__ == '__main__':
    HackathonDigitalTwin.run_server()
