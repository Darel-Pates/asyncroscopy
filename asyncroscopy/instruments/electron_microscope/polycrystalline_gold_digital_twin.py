"""Polycrystalline gold slab digital twin with corrector-derived STEM probes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import numpy as np
import pyTEMlib.image_tools as image_tools
import pyTEMlib.probe_tools as probe_tools
from scipy import ndimage
from tango.server import command, device_property

from asyncroscopy.data.data_writer import save_acquisition
from asyncroscopy.instruments.electron_microscope.digital_twin import DigitalTwin


@dataclass(frozen=True)
class RegionParameters:
    label: int
    occupied: bool
    orientation_euler_deg: tuple[float, float, float] | None
    composition: dict[str, float]
    voxel_count: int


def ceos_aberrations_to_probe_parameters(
    coefficients: dict,
    *,
    fov_nm: float,
    acceleration_voltage_ev: float,
    convergence_angle_mrad: float,
) -> dict:
    """Map CEOS tableau names/meters onto pyTEMlib probe coefficients/nm."""
    aberrations = probe_tools.get_target_aberrations(
        "Spectra300", int(acceleration_voltage_ev)
    )
    mappings = {
        "C1": ("C10",),
        "A1": ("C12a", "C12b"),
        "B2": ("C21a", "C21b"),
        "A2": ("C23a", "C23b"),
        "C3": ("C30",),
        "S3": ("C32a", "C32b"),
        "A3": ("C34a", "C34b"),
        "D4": ("C41a", "C41b"),
        "B4": ("C43a", "C43b"),
        "A4": ("C45a", "C45b"),
    }
    for source, destinations in mappings.items():
        if source not in coefficients:
            continue
        values = np.atleast_1d(coefficients[source]).astype(float)
        if len(values) != len(destinations):
            raise ValueError(
                f"Corrector coefficient {source} has {len(values)} value(s); "
                f"expected {len(destinations)}"
            )
        for destination, value_m in zip(destinations, values):
            aberrations[destination] = float(value_m * 1e9)

    aberrations["acceleration_voltage"] = float(acceleration_voltage_ev)
    aberrations["FOV"] = float(fov_nm)
    aberrations["convergence_angle"] = float(convergence_angle_mrad)
    aberrations["wavelength"] = image_tools.get_wavelength(
        aberrations["acceleration_voltage"]
    )
    return aberrations


def generate_polycrystalline_gold_slab(
    shape_zyx: tuple[int, int, int],
    *,
    voxel_size_nm: float,
    region_count: int,
    empty_region_fraction: float,
    minimum_gold_fraction: float,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, list[RegionParameters]]:
    """Generate labeled blobby regions and an orientation-dependent 3D potential."""
    if min(shape_zyx) < 4:
        raise ValueError("Every volume dimension must contain at least four voxels")
    if region_count < 2:
        raise ValueError("region_count must be at least 2")
    if not 0.0 <= empty_region_fraction < 1.0:
        raise ValueError("empty_region_fraction must be in [0, 1)")
    if not 0.0 < minimum_gold_fraction <= 1.0:
        raise ValueError("minimum_gold_fraction must be in (0, 1]")

    rng = np.random.default_rng(int(seed))
    nz, ny, nx = (int(value) for value in shape_zyx)
    z_norm = np.linspace(-1.0, 1.0, nz, dtype=np.float32)[:, None, None]
    y_norm = np.linspace(-1.0, 1.0, ny, dtype=np.float32)[None, :, None]
    x_norm = np.linspace(-1.0, 1.0, nx, dtype=np.float32)[None, None, :]

    labels = np.zeros((nz, ny, nx), dtype=np.int16)
    best_score = np.full((nz, ny, nx), np.inf, dtype=np.float32)
    centers = rng.uniform(-0.9, 0.9, size=(region_count, 3))
    axes = rng.uniform(0.30, 0.75, size=(region_count, 3))

    # Competing anisotropic distance fields form a full tessellation. Smooth
    # trigonometric perturbations make the interfaces blobby instead of planar.
    for index, (center, scale) in enumerate(zip(centers, axes), start=1):
        dz = (z_norm - center[0]) / scale[0]
        dy = (y_norm - center[1]) / scale[1]
        dx = (x_norm - center[2]) / scale[2]
        phase = rng.uniform(0.0, 2.0 * np.pi, size=3)
        roughness = 0.18 * (
            np.sin(3.1 * x_norm + 2.3 * y_norm + phase[0])
            + np.sin(2.7 * y_norm - 2.1 * z_norm + phase[1])
            + np.sin(2.5 * z_norm + 1.9 * x_norm + phase[2])
        )
        score = dz * dz + dy * dy + dx * dx + roughness
        update = score < best_score
        best_score[update] = score[update]
        labels[update] = index

    empty_count = max(1, int(round(region_count * empty_region_fraction)))
    empty_labels = set(
        int(value)
        for value in rng.choice(
            np.arange(1, region_count + 1), size=empty_count, replace=False
        )
    )

    z_nm = (np.arange(nz, dtype=np.float32) - (nz - 1) / 2) * voxel_size_nm
    y_nm = (np.arange(ny, dtype=np.float32) - (ny - 1) / 2) * voxel_size_nm
    x_nm = (np.arange(nx, dtype=np.float32) - (nx - 1) / 2) * voxel_size_nm
    zz, yy, xx = np.meshgrid(z_nm, y_nm, x_nm, indexing="ij", sparse=True)
    potential = np.zeros(labels.shape, dtype=np.float32)
    parameters: list[RegionParameters] = []
    lattice_parameter_nm = 0.4078

    for label in range(1, region_count + 1):
        mask = labels == label
        voxel_count = int(mask.sum())
        if label in empty_labels:
            labels[mask] = 0
            parameters.append(
                RegionParameters(label, False, None, {}, voxel_count)
            )
            continue

        euler = tuple(float(value) for value in rng.uniform(0.0, 360.0, size=3))
        gold_fraction = float(rng.uniform(minimum_gold_fraction, 1.0))
        composition = {"Au": gold_fraction, "Ag": 1.0 - gold_fraction}

        alpha, beta, gamma = np.deg2rad(euler)
        ca, sa = np.cos(alpha), np.sin(alpha)
        cb, sb = np.cos(beta), np.sin(beta)
        cg, sg = np.cos(gamma), np.sin(gamma)
        rotation = np.array(
            [
                [cg * cb, cg * sb * sa - sg * ca, cg * sb * ca + sg * sa],
                [sg * cb, sg * sb * sa + cg * ca, sg * sb * ca - cg * sa],
                [-sb, cb * sa, cb * ca],
            ],
            dtype=np.float32,
        )
        reciprocal_axes = rotation * (2.0 * np.pi / lattice_parameter_nm)
        phases = rng.uniform(0.0, 2.0 * np.pi, size=3)
        crystalline = np.zeros(labels.shape, dtype=np.float32)
        for axis, phase in zip(reciprocal_axes, phases):
            crystalline += np.cos(
                axis[0] * xx + axis[1] * yy + axis[2] * zz + phase
            ).astype(np.float32)
        crystalline = np.clip(0.55 + 0.15 * crystalline, 0.05, 1.0)
        effective_z = gold_fraction * 79.0 + (1.0 - gold_fraction) * 47.0
        amplitude = float((effective_z / 79.0) ** 1.7)
        potential[mask] = amplitude * crystalline[mask]
        parameters.append(
            RegionParameters(label, True, euler, composition, voxel_count)
        )

    return labels, potential, parameters


class PolycrystallineGoldDigitalTwin(DigitalTwin):
    """Voxel-slab HAADF twin whose probe is supplied by a corrector device."""

    volume_size_xy_nm = device_property(dtype=float, default_value=80.0)
    volume_thickness_nm = device_property(dtype=float, default_value=24.0)
    voxel_size_nm = device_property(dtype=float, default_value=0.5)
    region_count = device_property(dtype=int, default_value=18)
    empty_region_fraction = device_property(dtype=float, default_value=0.22)
    minimum_gold_fraction = device_property(dtype=float, default_value=0.90)
    acceleration_voltage_ev = device_property(dtype=float, default_value=200_000.0)
    convergence_angle_mrad = device_property(dtype=float, default_value=30.0)
    haadf_poisson_counts = device_property(dtype=float, default_value=2.0e6)

    def init_device(self) -> None:
        super().init_device()
        self._fov = float(self.volume_size_xy_nm) * 1e-9
        self._manufacturer = "UTKTeam Polycrystalline Gold Digital Twin"

    def _generate_sample(self, seed: int) -> None:
        voxel_size_nm = float(self.voxel_size_nm)
        xy = max(4, int(round(float(self.volume_size_xy_nm) / voxel_size_nm)))
        z = max(4, int(round(float(self.volume_thickness_nm) / voxel_size_nm)))
        labels, potential, parameters = generate_polycrystalline_gold_slab(
            (z, xy, xy),
            voxel_size_nm=voxel_size_nm,
            region_count=int(self.region_count),
            empty_region_fraction=float(self.empty_region_fraction),
            minimum_gold_fraction=float(self.minimum_gold_fraction),
            seed=int(seed),
        )
        self._region_label_volume = labels
        self._potential_volume = potential
        self._projected_potential = potential.sum(axis=0) * voxel_size_nm
        self._region_parameters = parameters
        half_xy_ang = float(self.volume_size_xy_nm) * 5.0
        half_z_ang = float(self.volume_thickness_nm) * 5.0
        self._world_bounds_ang = {
            "x_min": -half_xy_ang,
            "x_max": half_xy_ang,
            "y_min": -half_xy_ang,
            "y_max": half_xy_ang,
            "z_min": -half_z_ang,
            "z_max": half_z_ang,
        }
        self._all_sample_elements = ["Au", "Ag"]
        self._particle_records_base = []
        self._particle_records_view = []

    def _corrector_coefficients(self) -> dict:
        corrector = self._detector_proxies.get("corrector")
        if corrector is None:
            raise RuntimeError(
                "PolycrystallineGoldDigitalTwin requires corrector_device_address"
            )
        coefficients = json.loads(corrector.get_aberrations_coeff_sim())
        if not coefficients:
            raise RuntimeError("Corrector returned no simulation aberrations")
        return coefficients

    def _set_defocus(self, defocus) -> None:
        coefficients = self._corrector_coefficients()
        coefficients["C1"] = [float(defocus)]
        self._detector_proxies["corrector"].set_aberrations_coeff_sim(
            json.dumps(coefficients)
        )
        self._defocus = float(defocus)

    def _get_defocus(self) -> float:
        coefficients = self._corrector_coefficients()
        return float(coefficients.get("C1", [self._defocus])[0])

    def _sample_projected_potential(
        self, size: int, fov_nm: float, stage_x_nm: float, stage_y_nm: float
    ) -> np.ndarray:
        ny, nx = self._projected_potential.shape
        voxel_nm = float(self.voxel_size_nm)
        half_width_px = fov_nm / (2.0 * voxel_nm)
        center_x = (nx - 1) / 2.0 + stage_x_nm / voxel_nm
        center_y = (ny - 1) / 2.0 + stage_y_nm / voxel_nm
        x = np.linspace(center_x - half_width_px, center_x + half_width_px, size)
        y = np.linspace(center_y - half_width_px, center_y + half_width_px, size)
        yy, xx = np.meshgrid(y, x, indexing="ij")
        sampled = ndimage.map_coordinates(
            self._projected_potential,
            [yy, xx],
            order=1,
            mode="constant",
            cval=0.0,
        )
        sampled -= sampled.min()
        maximum = float(sampled.max())
        return (sampled / maximum if maximum > 0 else sampled).astype(np.float32)

    def _render_stem_image(
        self, imsize: int, dwell_time: float, detector_list: list
    ) -> np.ndarray:
        self._sync_stage_from_proxy()
        self._imsize = int(imsize)
        edge_crop = max(12, int(round(0.06 * imsize)))
        padded_size = int(imsize) + 2 * edge_crop
        fov_nm = float(self._fov) * 1e9
        padded_fov_nm = fov_nm * padded_size / int(imsize)
        stage_x_nm, stage_y_nm = self._stage_position[:2] * 1e9
        projected = self._sample_projected_potential(
            padded_size, padded_fov_nm, stage_x_nm, stage_y_nm
        )

        coefficients = self._corrector_coefficients()
        aberrations = ceos_aberrations_to_probe_parameters(
            coefficients,
            fov_nm=padded_fov_nm,
            acceleration_voltage_ev=float(self.acceleration_voltage_ev),
            convergence_angle_mrad=float(self.convergence_angle_mrad),
        )
        probe, _aperture, _chi = probe_tools.get_probe(
            aberrations, padded_size, padded_size, verbose=False
        )
        psf = np.fft.ifftshift(np.asarray(probe, dtype=np.float32))
        image = np.fft.ifft2(np.fft.fft2(projected) * np.fft.fft2(psf)).real
        image = np.clip(image, 0.0, None)
        image = image[edge_crop:-edge_crop, edge_crop:-edge_crop]
        image -= image.min()
        maximum = float(image.max())
        if maximum > 0:
            image /= maximum

        pose_seed = int(
            abs(
                hash(
                    (
                        int(self.sample_seed),
                        int(imsize),
                        round(fov_nm, 6),
                        tuple(np.round(self._stage_position, 12)),
                    )
                )
            )
            % (2**32)
        )
        rng = np.random.default_rng(pose_seed)
        counts = max(
            float(self.haadf_poisson_counts),
            float(dwell_time) * imsize * imsize * 1e9,
        )
        noisy = rng.poisson(image * counts).astype(np.float32) / counts
        noisy -= noisy.min()
        maximum = float(noisy.max())
        if maximum > 0:
            noisy /= maximum
        return noisy.astype(np.float32)

    def _acquisition_metadata(self) -> dict:
        occupied = sum(parameter.occupied for parameter in self._region_parameters)
        return {
            "sample_type": "polycrystalline_gold_slab",
            "volume_shape_zyx": list(self._potential_volume.shape),
            "voxel_size_nm": float(self.voxel_size_nm),
            "volume_size_xy_nm": float(self.volume_size_xy_nm),
            "volume_thickness_nm": float(self.volume_thickness_nm),
            "region_count": len(self._region_parameters),
            "occupied_region_count": int(occupied),
            "empty_region_count": int(len(self._region_parameters) - occupied),
            "defocus_m": self._get_defocus(),
            "corrector_aberrations": self._corrector_coefficients(),
            "probe_backend": "pyTEMlib.probe_tools.get_probe",
            "acceleration_voltage_ev": float(self.acceleration_voltage_ev),
            "convergence_angle_mrad": float(self.convergence_angle_mrad),
        }

    def _acquire_scanned_image(
        self,
        imsize: int,
        dwell_time: float,
        detector_list: list[str] = ["haadf"],
        scan_region: list[float] = [0.0, 0.0, 1.0, 1.0],
        output_format: str = ".h5",
    ) -> str:
        detector_list = [detector.upper() for detector in detector_list]
        images = [
            self._render_stem_image(int(imsize), float(dwell_time), [detector])
            for detector in detector_list
        ]
        metadata = self._acquisition_metadata()
        attrs = [metadata.copy() for _ in images]
        return save_acquisition(
            self,
            self._detector_proxies.get("data"),
            "stem_image",
            detector_list,
            images,
            dataset_attrs=attrs,
            file_attrs=metadata,
            output_format=output_format,
        )

    @command(dtype_out=str)
    def get_volume_metadata(self) -> str:
        metadata = self._acquisition_metadata()
        metadata["occupied_voxel_fraction"] = float(
            np.count_nonzero(self._region_label_volume)
            / self._region_label_volume.size
        )
        return json.dumps(metadata)

    @command(dtype_out=str)
    def get_region_parameters(self) -> str:
        return json.dumps([asdict(parameter) for parameter in self._region_parameters])


if __name__ == "__main__":
    PolycrystallineGoldDigitalTwin.run_server()
