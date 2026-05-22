"""DATA Tango device.

This device is the Tango bridge to the Tiled HTTP data server. It stores the
server URI, acquisition save path, and API key used by notebooks and microscope
devices.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from tango import AttrWriteType, DevState
from tango.server import Device, attribute, command

DEFAULT_TILED_URI = "http://10.46.217.241:9091"
DEFAULT_ACQUISITION_DIR = "outputs/tiled_acquisitions"
DEFAULT_REGISTER_TIMEOUT_SECONDS = 10.0
ONE_NODE_PER_FILE_WALKER = "tiled.client.register:one_node_per_item"


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
    tiled_server = attribute(
        label="Tiled Server",
        dtype=str,
        access=AttrWriteType.READ,
        doc="yes if the configured Tiled HTTP data server responds, otherwise no.",
    )

    def init_device(self) -> None:
        Device.init_device(self)
        self.set_state(DevState.ON)
        self._host, self._port = self._parse_uri(
            os.environ.get("ASYNCROSCOPY_TILED_URI", DEFAULT_TILED_URI)
        )
        self._save_path = os.environ.get(
            "ASYNCROSCOPY_ACQUISITION_DIR", DEFAULT_ACQUISITION_DIR
        )
        self._root_path = os.environ.get("ASYNCROSCOPY_TILED_ROOT_PATH", "").strip("/")
        self._api_key = os.environ.get("TILED_API_KEY")
        self._tiled_process = None
        self._tiled_watch_process = None
        self._tiled_server = "yes" if self._tiled_alive() else "no"
        self._tiled_server_status = ""
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

    def read_tiled_server(self) -> str:
        self._tiled_server = "yes" if self._tiled_alive() else "no"
        return self._tiled_server

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

    @command(dtype_out=str)
    def start_tiled_server(self) -> str:
        if self._tiled_alive():
            self._tiled_server = "yes"
            self._ensure_tiled_watcher()
            return self.get_config()

        catalog = _path_text(
            Path(self._save_path).expanduser() / ".asyncroscopy_tiled_catalog.db"
        )
        api_key = self._api_key or os.environ.get("TILED_API_KEY", "secret")
        try:
            if not (
                _looks_like_windows_drive_path(self._save_path) and os.name != "nt"
            ):
                Path(self._save_path).expanduser().mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    self._tiled_executable(),
                    "catalog",
                    "init",
                    "--if-not-exists",
                    catalog,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except Exception as exc:
            self._tiled_server = "no"
            self._tiled_server_status = str(exc)
            return self.get_config()

        command = [
            self._tiled_executable(),
            "serve",
            "catalog",
            catalog,
            "--read",
            self._save_path,
            "--public",
            "--api-key",
            api_key,
            "--host",
            self._host,
            "--port",
            str(self._port),
        ]
        self._tiled_process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not self._tiled_alive():
            if self._tiled_process.poll() is not None:
                break
            time.sleep(0.5)
        self._tiled_server = "yes" if self._tiled_alive() else "no"
        if self._tiled_server == "yes":
            self._ensure_tiled_watcher(api_key=api_key)
        else:
            self._tiled_server_status = (
                f"not running; exit_code={self._tiled_process.poll()}"
            )
        return self.get_config()

    @command(dtype_in=str, dtype_out=str)
    def register_path(self, path: str) -> str:
        result = self._register_path(path.strip())
        return json.dumps(result)

    @command(dtype_out=str)
    def get_recent(self) -> str:
        return json.dumps({"save_path": self._save_path, "files": self._recent_files()})

    @command(dtype_in=str, dtype_out=str)
    def path_exists(self, path: str) -> str:
        is_windows_path = _looks_like_windows_drive_path(path)
        candidate = (
            PureWindowsPath(path) if is_windows_path else Path(path).expanduser()
        )
        if not is_windows_path and not candidate.is_absolute():
            candidate = Path(self._save_path).expanduser() / candidate

        exists = (
            False if is_windows_path and os.name != "nt" else Path(candidate).exists()
        )
        return json.dumps(
            {
                "path": _path_text(candidate),
                "exists": exists,
                "is_file": Path(candidate).is_file() if exists else False,
                "size_bytes": Path(candidate).stat().st_size
                if exists and Path(candidate).is_file()
                else None,
                "note": "Windows drive path cannot be checked from this non-Windows process."
                if is_windows_path and os.name != "nt"
                else "",
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
            "tiled_server": self._tiled_server,
            "tiled_server_status": self._tiled_server_status,
        }

    def _uri(self) -> str:
        return f"http://{self._host}:{self._port}"

    def _tiled_alive(self) -> bool:
        try:
            with urlopen(self._uri(), timeout=0.3):
                return True
        except (OSError, URLError):
            return False

    def _ensure_tiled_watcher(self, api_key: str | None = None) -> None:
        if (
            self._tiled_watch_process is not None
            and self._tiled_watch_process.poll() is None
        ):
            self._tiled_server_status = "running; watcher active"
            return

        api_key = api_key or self._api_key or os.environ.get("TILED_API_KEY", "secret")
        command = self._register_command(self._save_path, api_key, watch=True)
        self._tiled_watch_process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True
        )
        time.sleep(0.5)
        self._tiled_server_status = (
            "running; watcher started"
            if self._tiled_watch_process.poll() is None
            else "running; watcher failed"
        )

    def _register_path(self, path: str) -> dict[str, Any]:
        tiled_key = self._tiled_key_for_path(path)
        timeout = float(
            os.environ.get(
                "ASYNCROSCOPY_TILED_REGISTER_TIMEOUT", DEFAULT_REGISTER_TIMEOUT_SECONDS
            )
        )
        try:
            self._register_with_tiled_client(path, timeout)
        except TimeoutError:
            self._tiled_server_status = (
                f"running; register path timed out after {timeout:g}s"
            )
            return {
                "path": path,
                "tiled_key": tiled_key,
                "registered": False,
                "timed_out": True,
                "timeout_seconds": timeout,
                "returncode": None,
                "output": "",
            }
        except Exception as exc:
            output = str(exc)[-1000:]
            self._tiled_server_status = f"running; register path failed; {output}"
            return {
                "path": path,
                "tiled_key": tiled_key,
                "registered": False,
                "timed_out": False,
                "returncode": None,
                "output": output,
            }

        self._tiled_server_status = "running; registered path"
        return {
            "path": path,
            "tiled_key": tiled_key,
            "registered": True,
            "timed_out": False,
            "returncode": 0,
            "output": "",
        }

    def _register_with_tiled_client(self, path: str, timeout: float) -> None:
        asyncio.run(
            asyncio.wait_for(self._register_with_tiled_client_async(path), timeout)
        )

    async def _register_with_tiled_client_async(self, path: str) -> None:
        from tiled.client import from_uri
        from tiled.client.register import identity, register

        client = from_uri(
            self._uri(),
            api_key=self._api_key or os.environ.get("TILED_API_KEY", "secret"),
        )
        await register(
            client,
            path,
            prefix=self._root_path or "/",
            walkers=[ONE_NODE_PER_FILE_WALKER],
            key_from_filename=identity,
        )

    def _register_command(self, path: str, api_key: str, watch: bool) -> list[str]:
        command = [
            self._tiled_executable(),
            "register",
            self._uri(),
            path,
            "--api-key",
            api_key,
            "--keep-ext",
            "--walker",
            ONE_NODE_PER_FILE_WALKER,
        ]
        if watch:
            command.append("--watch")
        if self._root_path:
            command.extend(["--prefix", self._root_path])
        return command

    def _tiled_key_for_path(self, path: str) -> str:
        name = (
            PureWindowsPath(path).name
            if _looks_like_windows_drive_path(path)
            else Path(path).name
        )
        return f"{self._root_path}/{name}" if self._root_path else name

    def _recent_files(self) -> list[dict[str, Any]]:
        root = Path(self._save_path).expanduser()
        if not root.exists():
            return []

        files = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
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
    def _parse_uri(uri: str) -> tuple[str, int]:
        without_scheme = uri.split("://", 1)[-1].strip("/")
        host, _, port = without_scheme.partition(":")
        return host or "10.46.217.241", int(port or 9091)

    @staticmethod
    def _tiled_executable() -> str:
        candidate = Path(sys.executable).with_name("tiled")
        return str(candidate) if candidate.exists() else "tiled"


def _looks_like_windows_drive_path(value: str) -> bool:
    return (
        len(value) >= 3
        and value[1] == ":"
        and value[0].isalpha()
        and value[2] in {"\\", "/"}
    )


def _is_windows_drive_path(path: Path | PureWindowsPath) -> bool:
    return isinstance(path, PureWindowsPath) or _looks_like_windows_drive_path(
        str(path)
    )


def _path_text(path: Path | PureWindowsPath) -> str:
    return str(path).replace("\\", "/") if _is_windows_drive_path(path) else str(path)


if __name__ == "__main__":
    DATA.run_server()
