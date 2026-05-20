"""DATA Tango device.

This device is the Tango bridge to the Tiled HTTP data server. It stores the
server URI, acquisition save path, and API key used by notebooks and microscope
devices.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any

import numpy as np
from tango import AttrWriteType, DevState
from tango.server import Device, attribute, command

DEFAULT_TILED_URI = "http://10.46.217.241:9091"
DEFAULT_ACQUISITION_DIR = "outputs/tiled_acquisitions"


def acquisition_config(
    *,
    save_directory: str | os.PathLike[str] | None = None,
    file_format: str | None = None,
) -> dict[str, str]:
    return {
        "save_directory": str(save_directory or os.environ.get("ASYNCROSCOPY_ACQUISITION_DIR") or DEFAULT_ACQUISITION_DIR),
        "file_format": (file_format or os.environ.get("ASYNCROSCOPY_ACQUISITION_FORMAT") or "tiff").lower().lstrip("."),
    }


def save_adorned_acquisition(adorned: Any, *, acquisition_type: str, detector: str, config: dict[str, str]) -> str:
    save_directory = _path_from_user(config["save_directory"])
    if isinstance(save_directory, Path):
        save_directory.mkdir(parents=True, exist_ok=True)
        _verify_writable_directory(save_directory)

    file_format = config.get("file_format", "tiff").lower().lstrip(".")
    if file_format not in {"tiff", "tif"}:
        raise ValueError(f"Unsupported acquisition file format: {file_format}")

    path = save_directory / f"{_safe_name(acquisition_type)}_{_safe_name(detector)}_{_stamp(time.time())}_{uuid.uuid4().hex[:8]}.tiff"
    saved_path = _save_with_native_adorned_writer(adorned, path)
    if not _path_exists(saved_path) and not (_is_windows_drive_path(saved_path) and os.name != "nt"):
        raise FileNotFoundError(f"Acquisition save returned without creating a file. Expected path: {_path_text(saved_path)}.")
    return _path_text(saved_path)


def saved_path_candidates(saved_path: str, save_directory: str, tiled_root_path: str = "") -> list[str]:
    saved = str(saved_path).replace("\\", "/")
    save_root = str(save_directory).replace("\\", "/").rstrip("/")
    root = tiled_root_path.strip("/")
    relative = saved[len(save_root) + 1 :] if save_root and saved.lower().startswith(save_root.lower() + "/") else _path_name(saved)
    candidates = _tiled_path_candidates(relative, root)
    if _path_name(saved) != relative:
        candidates.extend(_tiled_path_candidates(_path_name(saved), root))
    return list(dict.fromkeys(candidate.strip("/") for candidate in candidates if candidate))


def connect_tiled_client(uri: str | None = None, api_key: str | None = None):
    from tiled.client import from_uri

    uri = uri or os.environ.get("ASYNCROSCOPY_TILED_URI") or DEFAULT_TILED_URI
    api_key = api_key if api_key is not None else os.environ.get("TILED_API_KEY")
    return from_uri(uri, **({"api_key": api_key} if api_key else {}))


class DATA(Device):
    """Tango bridge to the Tiled HTTP data server."""

    host = attribute(
        label="Tiled Host",
        dtype=str,
        access=AttrWriteType.READ_WRITE,
        doc="Hostname or IP address for the Tiled HTTP data server.",
    )
    port = attribute(
        label="Tiled Port",
        dtype=int,
        access=AttrWriteType.READ_WRITE,
        doc="TCP port for the Tiled HTTP data server.",
    )
    save_path = attribute(
        label="Acquisition Save Path",
        dtype=str,
        access=AttrWriteType.READ_WRITE,
        doc="Directory where acquisition files are written and served by Tiled.",
    )
    root_path = attribute(
        label="Tiled Root Path",
        dtype=str,
        access=AttrWriteType.READ_WRITE,
        doc="Optional path prefix inside Tiled corresponding to save_path.",
    )

    def init_device(self) -> None:
        Device.init_device(self)
        self.set_state(DevState.ON)
        self._host, self._port = self._parse_uri(os.environ.get("ASYNCROSCOPY_TILED_URI", DEFAULT_TILED_URI))
        self._save_path = os.environ.get("ASYNCROSCOPY_ACQUISITION_DIR", DEFAULT_ACQUISITION_DIR)
        self._root_path = os.environ.get("ASYNCROSCOPY_TILED_ROOT_PATH", "").strip("/")
        self._api_key = os.environ.get("TILED_API_KEY")
        self.info_stream("DATA device initialised")

    def read_host(self) -> str:
        return self._host

    def write_host(self, value: str) -> None:
        self._host = value.strip()

    def read_port(self) -> int:
        return self._port

    def write_port(self, value: int) -> None:
        self._port = int(value)

    def read_save_path(self) -> str:
        return self._save_path

    def write_save_path(self, value: str) -> None:
        self._save_path = value

    def read_root_path(self) -> str:
        return self._root_path

    def write_root_path(self, value: str) -> None:
        self._root_path = value.strip("/")

    @command(dtype_out=str)
    def get_uri(self) -> str:
        return self._uri()

    @command(dtype_out=str)
    def get_config(self) -> str:
        return json.dumps(self._config())

    @command(dtype_in=str, dtype_out=str)
    def configure(self, config_json: str) -> str:
        config = json.loads(config_json) if config_json else {}
        for key, writer in {
            "host": self.write_host,
            "port": self.write_port,
            "save_path": self.write_save_path,
            "root_path": self.write_root_path,
        }.items():
            if key in config:
                writer(config[key])
        return self.get_config()

    @command(dtype_in=str, dtype_out=str)
    def set_api_key(self, api_key: str) -> str:
        self._api_key = api_key
        return self.get_config()

    @command(dtype_out=str)
    def clear_api_key(self) -> str:
        self._api_key = None
        return self.get_config()

    @command(dtype_in=str, dtype_out=str)
    def list_entries(self, path: str = "") -> str:
        return json.dumps({"path": path, "entries": list(self._node_for_path(path))})

    @command(dtype_out=str)
    def list_root(self) -> str:
        return self.list_entries("")

    @command(dtype_in=str, dtype_out=str)
    def get_data(self, saved_path_or_tiled_path: str) -> str:
        return json.dumps(self._json_ready(self._read_node(self._node_for_path_or_saved_path(saved_path_or_tiled_path))))

    @command(dtype_out=str)
    def get_recent(self) -> str:
        return json.dumps({"save_path": self._save_path, "files": self._recent_files()})

    @command(dtype_in=str, dtype_out=str)
    def path_exists(self, path: str) -> str:
        is_windows_path = _looks_like_windows_drive_path(path)
        candidate = PureWindowsPath(path) if is_windows_path else Path(path).expanduser()
        if not is_windows_path and not candidate.is_absolute():
            candidate = Path(self._save_path).expanduser() / candidate

        exists = False if is_windows_path and os.name != "nt" else Path(candidate).exists()
        return json.dumps(
            {
                "path": _path_text(candidate),
                "exists": exists,
                "is_file": Path(candidate).is_file() if exists else False,
                "size_bytes": Path(candidate).stat().st_size if exists and Path(candidate).is_file() else None,
                "note": "Windows drive path cannot be checked from this non-Windows process." if is_windows_path and os.name != "nt" else "",
            }
        )

    def _config(self) -> dict[str, Any]:
        return {
            "host": self._host,
            "port": self._port,
            "uri": self._uri(),
            "save_path": self._save_path,
            "root_path": self._root_path,
            "api_key_configured": bool(self._api_key),
        }

    def _uri(self) -> str:
        return f"http://{self._host}:{self._port}"

    def _client(self):
        return connect_tiled_client(self._uri(), api_key=self._api_key)

    def _node_for_path_or_saved_path(self, saved_path_or_tiled_path: str):
        client = self._client()
        candidates = [saved_path_or_tiled_path.strip(), *saved_path_candidates(saved_path_or_tiled_path.strip(), self._save_path, self._root_path)]
        errors = []
        for candidate in list(dict.fromkeys(candidates)):
            try:
                return self._walk_path(client, candidate)
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
        raise KeyError("Could not resolve path in Tiled. Tried: " + "; ".join(errors))

    def _node_for_path(self, tiled_path: str):
        return self._walk_path(self._client(), tiled_path)

    @staticmethod
    def _walk_path(node, tiled_path: str):
        for part in [piece for piece in tiled_path.strip("/").split("/") if piece]:
            node = node[part]
        return node

    @staticmethod
    def _read_node(node):
        if hasattr(node, "read"):
            return node.read()
        try:
            return node[:]
        except Exception:
            return node

    def _recent_files(self) -> list[dict[str, Any]]:
        root = Path(self._save_path).expanduser()
        if not root.exists():
            return []

        files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.stat().st_mtime, reverse=True)
        return [
            {
                "path": str(path),
                "file_name": path.name,
                "relative_path": str(path.relative_to(root)),
                "size_bytes": path.stat().st_size,
                "modified_time": path.stat().st_mtime,
            }
            for path in files[:20]
        ]

    @staticmethod
    def _json_ready(value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return {"type": "ndarray", "dtype": str(value.dtype), "shape": list(value.shape), "data": value.tolist()}
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, bytes):
            return {"type": "bytes", "encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if isinstance(value, dict):
            return {str(key): DATA._json_ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [DATA._json_ready(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, int]:
        without_scheme = uri.split("://", 1)[-1].strip("/")
        host, _, port = without_scheme.partition(":")
        return host or "10.46.217.241", int(port or 9091)


def _tiled_path_candidates(relative_path: str, root_path: str = "") -> list[str]:
    path = Path(relative_path)
    candidates = [str(path.with_suffix("")).replace(os.sep, "/"), str(path).replace(os.sep, "/")]
    return [f"{root_path}/{candidate}" if root_path else candidate for candidate in dict.fromkeys(candidates)]


def _save_with_native_adorned_writer(adorned: Any, path: Path | PureWindowsPath) -> Path | PureWindowsPath:
    result = adorned.save(_path_text(path))
    saved_path = _find_saved_path(path)
    if saved_path is not None:
        return saved_path
    if _is_windows_drive_path(path) and os.name != "nt":
        return path
    raise FileNotFoundError(f"AutoScript adorned.save did not create {_path_text(path)}. Return value: {result!r}.")


def _find_saved_path(path: Path | PureWindowsPath) -> Path | PureWindowsPath | None:
    candidates = [path]
    if _path_suffix(path).lower() == ".tiff":
        candidates.append(path.with_suffix(".tif"))
    if _path_suffix(path):
        candidates.append(path.with_suffix(""))
    return next((candidate for candidate in candidates if _path_exists(candidate)), None)


def _verify_writable_directory(path: Path) -> None:
    try:
        with tempfile.NamedTemporaryFile(prefix=".asyncroscopy_write_test_", dir=path, delete=True):
            pass
    except Exception as exc:
        raise PermissionError(f"Acquisition save directory is not writable: {path}") from exc


def _safe_name(value: str) -> str:
    return ("".join(char if char.isalnum() else "_" for char in str(value).strip().lower())).strip("_") or "acquisition"


def _stamp(timestamp: float) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(timestamp))


def _path_from_user(value: str | os.PathLike[str]) -> Path | PureWindowsPath:
    text = os.fspath(value)
    return PureWindowsPath(text) if _looks_like_windows_drive_path(text) else Path(text).expanduser().resolve()


def _looks_like_windows_drive_path(value: str) -> bool:
    return len(value) >= 3 and value[1] == ":" and value[0].isalpha() and value[2] in {"\\", "/"}


def _is_windows_drive_path(path: Path | PureWindowsPath) -> bool:
    return isinstance(path, PureWindowsPath) or _looks_like_windows_drive_path(str(path))


def _path_text(path: Path | PureWindowsPath) -> str:
    return str(path).replace("\\", "/") if _is_windows_drive_path(path) else str(path)


def _path_name(path: str) -> str:
    return PureWindowsPath(path).name if _looks_like_windows_drive_path(path) else Path(path).name


def _path_suffix(path: Path | PureWindowsPath) -> str:
    return PureWindowsPath(str(path)).suffix if _is_windows_drive_path(path) else path.suffix


def _path_exists(path: Path | PureWindowsPath) -> bool:
    return False if _is_windows_drive_path(path) and os.name != "nt" else Path(path).exists()


if __name__ == "__main__":
    DATA.run_server()
