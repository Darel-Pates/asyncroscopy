#!/usr/bin/env python
"""Start the local Tango database and all registered asyncroscopy servers."""

from __future__ import annotations

import importlib
import importlib.util
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import tango


DB_HOST_DEFAULT = "10.46.217.241"
DB_PORT_DEFAULT = 9094
DATABASE_TIMEOUT_SECONDS = 120
DEVICE_TIMEOUT_SECONDS = 120

PROJECT_DIR = Path(__file__).resolve().parents[1]
REGISTER_SCRIPT = PROJECT_DIR / "scripts" / "2_register_devices.py"

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


class Style:
    enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    reset = "\033[0m" if enabled else ""
    bold = "\033[1m" if enabled else ""
    dim = "\033[2m" if enabled else ""
    green = "\033[32m" if enabled else ""
    yellow = "\033[33m" if enabled else ""
    red = "\033[31m" if enabled else ""
    cyan = "\033[36m" if enabled else ""


@dataclass(frozen=True)
class DeviceServer:
    key: str
    server: str
    classname: str
    device: str
    module: str
    instance: str
    is_microscope: bool = False


@dataclass
class ManagedProcess:
    key: str
    label: str
    command: list[str]
    process: subprocess.Popen[bytes]
    started_at: float

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def running(self) -> bool:
        return self.process.poll() is None


def color(text: str, code: str) -> str:
    return f"{code}{text}{Style.reset}" if Style.enabled else text


def prompt_str(label: str, default: str) -> str:
    try:
        answer = input(f"{color(label, Style.bold)} [{default}]: ").strip()
    except EOFError:
        print(default)
        return default
    return answer or default


def prompt_int(label: str, default: int) -> int:
    while True:
        raw = prompt_str(label, str(default))
        try:
            return int(raw)
        except ValueError:
            print(f"  {color('Invalid number.', Style.red)} Please enter an integer or press Enter.")


def prompt_bool(label: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        try:
            answer = input(f"{color(label, Style.bold)} [{suffix}]: ").strip().lower()
        except EOFError:
            print("yes" if default else "no")
            return default
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print(f"  {color('Invalid choice.', Style.red)} Enter y, n, or press Enter.")


def banner(title: str) -> None:
    width = 78
    print()
    print(color("=" * width, Style.cyan))
    print(color(title.center(width), Style.bold + Style.cyan))
    print(color("=" * width, Style.cyan))


def section(step: int, total: int, title: str) -> None:
    print()
    print(color(f"[{step}/{total}] {title}", Style.bold))
    print(color("-" * 78, Style.dim))


def status_line(status: str, message: str, detail: str = "") -> None:
    colors = {"OK": Style.green, "RUN": Style.cyan, "WAIT": Style.yellow, "FAIL": Style.red, "SKIP": Style.dim}
    tag = color(f"{status:>4}", colors.get(status, ""))
    if detail:
        print(f"  {tag}  {message:<32} {color(detail, Style.dim)}")
    else:
        print(f"  {tag}  {message}")


def load_register_module():
    spec = importlib.util.spec_from_file_location("asyncroscopy_register_devices", REGISTER_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {REGISTER_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_module_for_class(classname: str) -> str:
    candidates = [
        f"asyncroscopy.{classname}",
        f"asyncroscopy.hardware.{classname}",
        f"asyncroscopy.detectors.{classname}",
        f"asyncroscopy.software.{classname}",
    ]
    for module_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(module, classname):
            return module_name
    raise RuntimeError(f"Could not find Python module for Tango class {classname!r}")


def load_device_servers() -> list[DeviceServer]:
    module = load_register_module()
    devices: list[DeviceServer] = []

    for name, value in sorted(vars(module).items()):
        if not name.endswith("_SERVER"):
            continue
        prefix = name[: -len("_SERVER")]
        classname = getattr(module, f"{prefix}_CLASS", None)
        device = getattr(module, f"{prefix}_DEVICE", None)
        if not classname or not device:
            continue

        server_class, _, instance = value.partition("/")
        if not server_class or not instance:
            raise RuntimeError(f"Invalid server declaration for {prefix}: {value!r}")

        key = device.split("/")[1]
        devices.append(
            DeviceServer(
                key=key,
                server=value,
                classname=classname,
                device=device,
                module=find_module_for_class(classname),
                instance=instance,
                is_microscope=classname.lower().endswith("microscope"),
            )
        )

    if not devices:
        raise RuntimeError(f"No device declarations found in {REGISTER_SCRIPT}")

    devices.sort(key=lambda item: (item.is_microscope, item.key))
    return devices


def make_env(host: str, port: int) -> dict[str, str]:
    tango_host = f"{host}:{port}"
    os.environ["TANGO_HOST"] = tango_host
    return {**os.environ, "TANGO_HOST": tango_host, "PYTHONUNBUFFERED": "1"}


def popen(key: str, label: str, command: list[str], env: dict[str, str]) -> ManagedProcess:
    process = subprocess.Popen(
        command,
        env=env,
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                os.set_blocking(stream.fileno(), False)
            except (AttributeError, OSError):
                pass
    return ManagedProcess(key=key, label=label, command=command, process=process, started_at=time.monotonic())


def run_capture(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, cwd=PROJECT_DIR, capture_output=True, text=True)


def read_stream(stream) -> str:
    if stream is None:
        return ""
    chunks: list[bytes] = []
    while True:
        try:
            chunk = stream.read(4096)
        except BlockingIOError:
            break
        except Exception:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode(errors="replace").strip()


def collect_output(managed: ManagedProcess) -> tuple[str, str]:
    return read_stream(managed.process.stdout), read_stream(managed.process.stderr)


def terminate_process(managed: ManagedProcess, timeout: float = 5.0) -> None:
    if not managed.running:
        return
    managed.process.terminate()
    try:
        managed.process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        managed.process.kill()
        managed.process.wait(timeout=timeout)


def terminate_all(processes: Iterable[ManagedProcess]) -> None:
    for managed in reversed(list(processes)):
        terminate_process(managed)


def process_port_pids(port: int) -> list[int]:
    if os.name == "nt":
        return process_port_pids_windows(port)

    try:
        result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
    except FileNotFoundError:
        status_line("SKIP", f"database port {port}", "lsof is not installed")
        return []
    return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]


def process_port_pids_windows(port: int) -> list[int]:
    try:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True)
    except FileNotFoundError:
        status_line("SKIP", f"database port {port}", "netstat is not available")
        return []

    pids: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address = parts[1]
        state = parts[3].upper()
        pid = parts[-1]
        if state == "LISTENING" and local_address.rsplit(":", 1)[-1] == str(port) and pid.isdigit():
            pids.add(int(pid))
    return sorted(pids)


def stop_pids(pids: Iterable[int]) -> int:
    stopped = 0
    for pid in pids:
        if os.name == "nt":
            try:
                result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
            except FileNotFoundError:
                status_line("SKIP", f"PID {pid}", "taskkill is not available")
                continue
            if result.returncode == 0:
                stopped += 1
            else:
                status_line("FAIL", f"PID {pid}", (result.stderr or result.stdout).strip())
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped += 1
            except ProcessLookupError:
                pass
            except PermissionError:
                status_line("FAIL", f"PID {pid}", "permission denied")
    return stopped


def pkill_pattern(pattern: str) -> bool:
    if os.name == "nt":
        return stop_windows_python_processes(pattern)

    try:
        result = subprocess.run(["pkill", "-f", pattern], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def stop_windows_python_processes(pattern: str) -> bool:
    powershell = "powershell.exe"
    script = (
        "$pattern = $args[0]; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($pattern) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", script, pattern],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def clear_old_processes(port: int, devices: list[DeviceServer]) -> None:
    pids = process_port_pids(port)
    stopped = stop_pids(pids)
    status_line("OK" if stopped else "SKIP", f"database port {port}", f"{stopped} process(es) signaled")

    stopped_servers = 0
    for device in devices:
        if pkill_pattern(f"{device.classname} {device.instance}"):
            stopped_servers += 1
    if pkill_pattern("ThermoMicroscope microscope_instance"):
        stopped_servers += 1
    status_line("OK" if stopped_servers else "SKIP", "old device servers", f"{stopped_servers} process group(s) signaled")
    time.sleep(2)


def wait_for_database(host: str, port: int, timeout: int) -> float:
    start = time.monotonic()
    last_error: Exception | None = None
    while time.monotonic() - start < timeout:
        try:
            db = tango.Database(host, port)
            db.get_db_host()
            return time.monotonic() - start
        except Exception as exc:
            last_error = exc
            print(color(".", Style.dim), end="", flush=True)
            time.sleep(1)
    raise TimeoutError(f"Tango database did not become ready after {timeout}s. Last error: {last_error}")


def wait_for_device(device_name: str, timeout: int, interval: float = 1.0) -> float:
    start = time.monotonic()
    last_error: Exception | None = None
    while time.monotonic() - start < timeout:
        try:
            proxy = tango.DeviceProxy(device_name)
            proxy.ping()
            return time.monotonic() - start
        except Exception as exc:
            last_error = exc
            print(color(".", Style.dim), end="", flush=True)
            time.sleep(interval)
    raise TimeoutError(f"{device_name} did not become ready after {timeout}s. Last error: {last_error}")


def print_process_debug(processes: Iterable[ManagedProcess]) -> None:
    print()
    print(color("Debug output", Style.bold + Style.yellow))
    print(color("-" * 78, Style.dim))
    for managed in processes:
        stdout, stderr = collect_output(managed)
        running = managed.running
        print(f"{color(managed.label, Style.bold)}  pid={managed.pid}  running={running}  returncode={managed.process.poll()}")
        print(f"  command: {' '.join(managed.command)}")
        print(f"  stdout: {stdout or '(empty)'}")
        print(f"  stderr: {stderr or '(empty)'}")
        print()


def print_inventory(devices: list[DeviceServer]) -> None:
    status_line("OK", "device inventory", f"{len(devices)} declaration(s) loaded from scripts/2_register_devices.py")
    width_key = max(len(item.key) for item in devices)
    width_class = max(len(item.classname) for item in devices)
    for item in devices:
        status_line("RUN", item.key.ljust(width_key), f"{item.classname.ljust(width_class)}  {item.device}")


def print_final_summary(host: str, port: int, processes: list[ManagedProcess], ready_times: dict[str, float]) -> None:
    section(5, 5, "Startup summary")
    print(f"  {color('TANGO_HOST', Style.bold):<18} {host}:{port}")
    print(f"  {color('PROJECT', Style.bold):<18} {PROJECT_DIR}")
    print()
    print(f"  {'SERVER':<14} {'PID':>8} {'READY':>10}  COMMAND")
    print(color("  " + "-" * 74, Style.dim))
    for managed in processes:
        ready = ready_times.get(managed.key)
        ready_text = f"{ready:.1f}s" if ready is not None else "-"
        print(f"  {managed.key:<14} {managed.pid:>8} {ready_text:>10}  {' '.join(managed.command)}")
    print()
    print(color("All asyncroscopy servers are ready.", Style.bold + Style.green))


def main() -> int:
    devices = load_device_servers()
    device_servers = [item for item in devices if not item.is_microscope]
    microscope_servers = [item for item in devices if item.is_microscope]

    banner("ASYNCROSCOPY SERVER STARTUP")
    print("Press Enter at any prompt to use the value shown in brackets.")
    print()

    host = prompt_str("Tango database host", DB_HOST_DEFAULT)
    port = prompt_int("Tango database port", DB_PORT_DEFAULT)
    clear_first = prompt_bool("Clear old processes first", True)
    start_database = prompt_bool("Start Tango database", True)
    register_devices = prompt_bool("Register devices", True)
    timeout = prompt_int("Device startup timeout seconds", DEVICE_TIMEOUT_SECONDS)

    env = make_env(host, port)
    processes: list[ManagedProcess] = []
    ready_times: dict[str, float] = {}

    print()
    print(f"  {color('TANGO_HOST', Style.bold):<18} {host}:{port}")
    print(f"  {color('PROJECT', Style.bold):<18} {PROJECT_DIR}")
    print_inventory(devices)

    try:
        section(1, 5, "Clearing old processes")
        if clear_first:
            clear_old_processes(port, devices)
        else:
            status_line("SKIP", "old process cleanup")

        section(2, 5, "Starting Tango database")
        if start_database:
            database = popen(
                "database",
                "Tango database",
                ["uv", "run", "python", "-m", "tango.databaseds.database", "2"],
                env,
            )
            processes.append(database)
            print("  WAIT  database readiness", end="", flush=True)
            elapsed = wait_for_database(host, port, DATABASE_TIMEOUT_SECONDS)
            ready_times["database"] = elapsed
            print(f" {color('OK', Style.green)} pid={database.pid} ready in {elapsed:.1f}s")
        else:
            print("  WAIT  existing database readiness", end="", flush=True)
            elapsed = wait_for_database(host, port, DATABASE_TIMEOUT_SECONDS)
            ready_times["database"] = elapsed
            print(f" {color('OK', Style.green)} ready in {elapsed:.1f}s")

        section(3, 5, "Registering devices")
        if register_devices:
            result = run_capture(["uv", "run", "python", str(REGISTER_SCRIPT)], env)
            if result.returncode != 0:
                print(result.stdout.strip())
                print(result.stderr.strip())
                raise RuntimeError("Device registration failed.")
            for line in result.stdout.splitlines():
                clean = line.strip()
                if clean.startswith("registered:"):
                    status_line("OK", clean.replace("registered:", "").strip())
                elif clean.startswith("property:"):
                    status_line("OK", clean)
                elif clean and clean not in {"Done!"}:
                    status_line("OK", clean)
        else:
            status_line("SKIP", "device registration")

        section(4, 5, "Starting device servers")
        for device in device_servers:
            command = ["uv", "run", "python", "-m", device.module, device.instance]
            managed = popen(device.key, device.classname, command, env)
            processes.append(managed)
            status_line("RUN", device.key, f"{device.module}  pid={managed.pid}")

        for device in device_servers:
            print(f"  WAIT  {device.device:<34}", end="", flush=True)
            elapsed = wait_for_device(device.device, timeout)
            ready_times[device.key] = elapsed
            print(f" {color('OK', Style.green)} ready in {elapsed:.1f}s")

        for device in microscope_servers:
            print()
            status_line("RUN", device.key, f"{device.module}  starting after dependencies")
            managed = popen(device.key, device.classname, ["uv", "run", "python", "-m", device.module, device.instance], env)
            processes.append(managed)
            print(f"  WAIT  {device.device:<34}", end="", flush=True)
            elapsed = wait_for_device(device.device, timeout)
            ready_times[device.key] = elapsed
            print(f" {color('OK', Style.green)} ready in {elapsed:.1f}s")

        print_final_summary(host, port, processes, ready_times)
        print()
        print(color("Leave this terminal open while you use the servers. Press Ctrl+C to stop them.", Style.dim))
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print()
        print(color("Shutdown requested. Stopping managed processes...", Style.yellow))
        terminate_all(processes)
        status_line("OK", "shutdown complete")
        return 0
    except Exception as exc:
        print()
        print(color(f"Startup failed: {exc}", Style.bold + Style.red))
        print_process_debug(processes)
        terminate_all(processes)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
