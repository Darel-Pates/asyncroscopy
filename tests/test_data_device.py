import json
import subprocess

import tango

from asyncroscopy.software.DATA import DATA


class TestDataDevice:
    def test_state_is_on(self, data_proxy: tango.DeviceProxy) -> None:
        assert data_proxy.state() == tango.DevState.ON

    def test_config_round_trip(self, data_proxy: tango.DeviceProxy, tmp_path) -> None:
        config = {
            "host": "127.0.0.1",
            "port": 9091,
            "save_path": str(tmp_path),
        }

        returned = json.loads(data_proxy.configure(json.dumps(config)))

        assert returned["host"] == config["host"]
        assert returned["port"] == config["port"]
        assert returned["save_path"] == config["save_path"]
        assert returned["uri"] == "http://127.0.0.1:9091"

    def test_start_tiled_server_uses_catalog_server_command(
        self,
        data_proxy: tango.DeviceProxy,
        monkeypatch,
        tmp_path,
    ) -> None:
        calls = []
        popen_calls = []
        run_commands = []

        def fake_alive(self):
            calls.append(None)
            return len(calls) > 1

        class FakeProcess:
            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            popen_calls.append({"command": command, "kwargs": kwargs})
            return FakeProcess()

        data_proxy.host = "127.0.0.1"
        data_proxy.port = 9091
        data_proxy.save_path = str(tmp_path)
        monkeypatch.setattr(DATA, "_tiled_alive", fake_alive)
        monkeypatch.setattr(DATA, "_tiled_executable", lambda self: "tiled")
        monkeypatch.setattr("asyncroscopy.software.DATA.subprocess.Popen", fake_popen)
        monkeypatch.setattr(
            "asyncroscopy.software.DATA.subprocess.run",
            lambda command, **_: (
                run_commands.append(command)
                or type("Result", (), {"returncode": 0, "stdout": ""})()
            ),
        )

        returned = json.loads(data_proxy.start_tiled_server())

        assert returned["tiled_server"] == "yes"
        key_value = popen_calls[0]["command"][8]
        assert key_value == popen_calls[1]["command"][5]
        assert popen_calls == [
            {
                "command": [
                    "tiled",
                    "serve",
                    "catalog",
                    str(tmp_path / ".asyncroscopy_tiled_catalog.db"),
                    "--read",
                    str(tmp_path),
                    "--public",
                    "--api-key",
                    key_value,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "9091",
                ],
                "kwargs": {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.STDOUT,
                    "text": True,
                },
            },
            {
                "command": [
                    "tiled",
                    "register",
                    "http://127.0.0.1:9091",
                    str(tmp_path),
                    "--api-key",
                    key_value,
                    "--keep-ext",
                    "--walker",
                    "tiled.client.register:one_node_per_item",
                    "--watch",
                ],
                "kwargs": {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.STDOUT,
                    "text": True,
                },
            },
        ]
        assert run_commands == [
            [
                "tiled",
                "catalog",
                "init",
                "--if-not-exists",
                str(tmp_path / ".asyncroscopy_tiled_catalog.db"),
            ],
        ]

    def test_register_path_registers_single_file(
        self,
        data_proxy: tango.DeviceProxy,
        monkeypatch,
        tmp_path,
    ) -> None:
        registrations = []
        saved = tmp_path / "frame.tiff"
        saved.write_bytes(b"fake-tiff")
        data_proxy.host = "127.0.0.1"
        data_proxy.port = 9091

        def fake_from_uri(*args, **kwargs):
            return object()

        async def fake_register(client, path, **kwargs):
            registrations.append(path)

        monkeypatch.setattr("asyncroscopy.software.DATA.from_uri", fake_from_uri)
        monkeypatch.setattr("asyncroscopy.software.DATA.register", fake_register)

        result = data_proxy.register_path(str(saved))

        assert result == "frame.tiff"
        assert registrations == [str(saved)]

    def test_register_path_returns_windows_tiled_key(
        self,
        data_proxy: tango.DeviceProxy,
        monkeypatch,
    ) -> None:
        windows_path = "D:/microscopedata/tiled/ahoust17/frame.tiff"
        data_proxy.host = "127.0.0.1"
        data_proxy.port = 9091

        def fake_from_uri(*args, **kwargs):
            return object()

        async def fake_register(*args, **kwargs):
            return None

        monkeypatch.setattr("asyncroscopy.software.DATA.from_uri", fake_from_uri)
        monkeypatch.setattr("asyncroscopy.software.DATA.register", fake_register)

        assert data_proxy.register_path(windows_path) == "frame.tiff"
