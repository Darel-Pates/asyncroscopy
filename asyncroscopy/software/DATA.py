"""DATA Tango device.

This device is the Tango bridge to the Tiled HTTP data server. It stores the
server URI and acquisition save path used by notebooks and microscope devices.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path, PureWindowsPath
from urllib.error import URLError
from urllib.request import urlopen

from tango import AttrWriteType, DevState
from tango.server import Device, attribute, command
from tiled.client import from_uri
from tiled.client.register import identity, register

DEFAULT_TILED_URI = "http://10.46.217.241:9091"
DEFAULT_ACQUISITION_DIR = "outputs/tiled_acquisitions"
ONE_NODE_PER_FILE_WALKER = "tiled.client.register:one_node_per_item"


class DATA(Device):
    """Tango bridge to the Tiled HTTP data server."""

    host = attribute(label="Tiled Host", dtype=str, access=AttrWriteType.READ_WRITE, doc="Hostname or IP address for the Tiled HTTP data server.")
    port = attribute(label="Tiled Port", dtype=int, access=AttrWriteType.READ_WRITE, doc="TCP port for the Tiled HTTP data server.")
    save_path = attribute(label="Acquisition Save Path", dtype=str, access=AttrWriteType.READ_WRITE, doc="Directory where acquisition files are written and served by Tiled.")
    tiled_server = attribute(label="Tiled Server", dtype=str, access=AttrWriteType.READ, doc="yes if the configured Tiled HTTP data server responds, otherwise no.")

    def init_device(self) -> None:
        Device.init_device(self)
        self.set_state(DevState.ON)
        self._host, self._port = self._parse_uri(os.environ.get("ASYNCROSCOPY_TILED_URI", DEFAULT_TILED_URI))
        self._save_path = os.environ.get("ASYNCROSCOPY_ACQUISITION_DIR", DEFAULT_ACQUISITION_DIR)
        self._api_key = "secret"
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

    def read_tiled_server(self) -> str:
        self._tiled_server = "yes" if self._tiled_alive() else "no"
        return self._tiled_server

    @command(dtype_out=str)
    def get_config(self) -> str:
        return json.dumps({"host": self._host, "port": self._port, "uri": self._uri(), "save_path": self._save_path, "tiled_server": self._tiled_server, "tiled_server_status": self._tiled_server_status})

    @command(dtype_in=str, dtype_out=str)
    def configure(self, config_json: str) -> str:
        config = json.loads(config_json) if config_json else {}
        for key, writer in {
            "host": self.write_host,
            "port": self.write_port,
            "save_path": self.write_save_path,
        }.items():
            if key in config:
                writer(config[key])
        return self.get_config()

    @command(dtype_out=str)
    def start_tiled_server(self, timeout = 30) -> str:
        if self._tiled_alive():
            self._tiled_server = "yes"
            self._ensure_tiled_watcher()
            return self.get_config()

        catalog = _path_text(Path(self._save_path).expanduser() / ".asyncroscopy_tiled_catalog.db")
        try:
            if not (_is_windows_drive_path(self._save_path) and os.name != "nt"):
                Path(self._save_path).expanduser().mkdir(parents=True, exist_ok=True)
            subprocess.run([self._tiled_executable(), "catalog", "init", "--if-not-exists", catalog], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as exc:
            self._tiled_server = "no"
            self._tiled_server_status = str(exc)
            return self.get_config()

        self._tiled_process = subprocess.Popen([self._tiled_executable(),"serve","catalog",catalog,"--read",self._save_path,"--public","--api-key",self._api_key,
                                                "--host",self._host,"--port",str(self._port)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._tiled_alive():
            if self._tiled_process.poll() is not None:
                break
            time.sleep(0.5)
        self._tiled_server = "yes" if self._tiled_alive() else "no"
        if self._tiled_server == "yes":
            self._ensure_tiled_watcher()
        else:
            self._tiled_server_status = f"not running; exit_code={self._tiled_process.poll()}"
        return self.get_config()

    @command(dtype_in=str, dtype_out=str)
    def register_path(self, path: str) -> str:
        path = path.strip()
        timeout = 10 # seconds

        async def register_with_tiled_client() -> None:
            client = from_uri(self._uri(), api_key=self._api_key)
            await register(client, path, walkers=[ONE_NODE_PER_FILE_WALKER], key_from_filename=identity)

        asyncio.run(asyncio.wait_for(register_with_tiled_client(), timeout))
        self._tiled_server_status = "running; registered path"
        return PureWindowsPath(path).name if _is_windows_drive_path(path) else Path(path).name

    def _uri(self) -> str:
        return f"http://{self._host}:{self._port}"

    def _tiled_alive(self) -> bool:
        try:
            with urlopen(self._uri(), timeout=0.3):
                return True
        except (OSError, URLError):
            return False

    def _ensure_tiled_watcher(self) -> None:
        if self._tiled_watch_process is not None and self._tiled_watch_process.poll() is None:
            self._tiled_server_status = "running; watcher active"
            return

        command = [self._tiled_executable(), "register", self._uri(), self._save_path, "--api-key", self._api_key, "--keep-ext", "--walker", ONE_NODE_PER_FILE_WALKER, "--watch"]
        self._tiled_watch_process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)
        time.sleep(0.5)
        self._tiled_server_status = "running; watcher started" if self._tiled_watch_process.poll() is None else "running; watcher failed"

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, int]:
        without_scheme = uri.split("://", 1)[-1].strip("/")
        host, _, port = without_scheme.partition(":")
        return host or "10.46.217.241", int(port or 9091)

    @staticmethod
    def _tiled_executable() -> str:
        candidate = Path(sys.executable).with_name("tiled")
        return str(candidate) if candidate.exists() else "tiled"


def _is_windows_drive_path(path: str | Path | PureWindowsPath) -> bool:
    text = str(path)
    return isinstance(path, PureWindowsPath) or (len(text) >= 3 and text[1] == ":" and text[0].isalpha() and text[2] in {"\\", "/"})


def _path_text(path: Path | PureWindowsPath) -> str:
    return str(path).replace("\\", "/") if _is_windows_drive_path(path) else str(path)


if __name__ == "__main__":
    DATA.run_server()
