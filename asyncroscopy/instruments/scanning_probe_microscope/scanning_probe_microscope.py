"""
Scanning probe microscope Tango device.

Thin orchestrator over the SPM sub-devices (SCAN, FEEDBACK, APPROACH,
STAGE, SPECTROSCOPY). Each sub-device owns its own parameters and its
vendor-specific behaviour; this device only wires them together and
exposes cross-device workflows and instrument-global state.

Return convention for acquisition commands
------------------------------------------
Acquisition commands return a string supplied by the sub-device,
typically a DATA/Tiled unique id.
"""

import enum
import json
from abc import abstractmethod

import tango
# from tango import AttrWriteType, DevFloat, DevVarFloatArray
# from tango.server import attribute, command, device_property

from asyncroscopy.instruments.instrument import Instrument

class SPMMode(enum.IntEnum):
    CONTACT_AFM = 0
    NON_CONTACT_AFM = 1
    KPFM = 2
    EFM = 3
    CONDUCTIVE_AFM = 4
    SF_PFM = 5
    DART = 6
    ESM = 7
    MFM = 8
    THERMAL = 9
    AFM_IR = 10
    TERS = 11
    SNOM = 12

class SPMMicroscope(Instrument):
    """
    Top-level scanning probe microscope device.

    Single-subsystem actions are delegated to the sub-devices via
    DeviceProxy; only instrument-global state (spm_mode, meter values)
    is implemented by the concrete vendor subclass.
    """

    # ------------------------------------------------------------------
    # Sub-device addresses — configure in Tango DB per deployment
    # ------------------------------------------------------------------

    scan_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the SCAN device. "
        "DB mode: 'asyncroscopy/scan/default' "
        "No-DB mode: 'tango://127.0.0.1:8888/asyncroscopy/scan/default#dbase=no'",
    )
    
    feedback_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the FEEDBACK device.",
    )

    approach_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the APPROACH device.",
    )

    stage_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the STAGE device.",
    )

    spectroscopy_device_address = tango.server.device_property(
        dtype=str,
        doc="Tango device address for the SPECTROSCOPY device.",
    )

    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------

    spm_mode = tango.server.attribute(
        label="SPM Mode",
        dtype=SPMMode,
        access=tango.AttrWriteType.READ,
        doc="Active SPM operating mode.",
    )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_device_attributes(self) -> None:
        self._device_proxies: dict[str, tango.DeviceProxy] = {}

    def read_instrument_type(self) -> str:
        return 'SPM'
    
    def read_spm_mode(self) -> SPMMode:
        return self._get_spm_mode()
    
    def _connect(self):
        self._connect_hardware()
        self._connect_device_proxies()
        self.set_state(tango.DevState.ON)

    def _connect_device_proxies(self) -> None:
        addresses = {
            'scan': self.scan_device_address,
            'feedback': self.feedback_device_address,
            'approach': self.approach_device_address,
            'stage': self.stage_device_address,
            'spectroscopy': self.spectroscopy_device_address,
        }
        for name, address in addresses.items():
            if address:
                self._device_proxies[name] = tango.DeviceProxy(address)
                self.info_stream(f'Connected proxy {name} -> {address}')

    def _disconnect(self):
        self._device_proxies = {}
        self.info_stream('Disconnected from sub-devices')

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_proxy(self, name: str) -> tango.DeviceProxy:
        """Return the sub-device proxy or raise a clear DevFailed."""
        proxy = self._device_proxies.get(name)
        if proxy is None:
            tango.Except.throw_exception(
                'DeviceNotConfigured',
                f"No '{name}' device is configured. "
                f"Set {name}_device_address in the Tango DB / config yaml.",
                f'{name}',
            )
        try:
            proxy.ping() # type: ignore
        except tango.DevFailed:
            tango.Except.throw_exception(
                'DeviceNotAccessible',
                f"The '{name}' device at '{proxy.dev_name()}' is not responding. " # type: ignore
                f"Check that its server is running.",
                f'{name}',
            )
        return proxy
    
    # ------------------------------------------------------------------
    # Commands — instrument-global
    # ------------------------------------------------------------------

    @tango.server.command(dtype_out=str)
    def get_microscope_state(self) -> str:
        """Aggregate instrument-global state and sub-device states as JSON."""
        devices = {}
        for name in ('scan', 'feedback', 'approach', 'stage', 'spectroscopy'):
            proxy = self._device_proxies.get(name)
            if proxy is None:
                devices[name] = 'NOT_CONFIGURED'
            else:
                try:
                    devices[name] = str(proxy.State())
                except tango.DevFailed:
                    devices[name] = 'UNREACHABLE'
        state = {'spm_mode': self.read_spm_mode().name, 'devices': devices}
        return json.dumps(state)
    

    @tango.server.command(dtype_out=str) #the only direct function
    def get_meter_values(self) -> str:
        """Read current meter values (Sum, Deflection, Lateral, Z) as JSON."""
        return json.dumps(self._get_meter_values())

    # ------------------------------------------------------------------
    # Commands — delegators to sub-devices
    # ------------------------------------------------------------------
    @tango.server.command(dtype_out=str)
    def acquire_scan(self) -> str:
        """Acquire a scan using SCAN device settings; returns a DATA/Tiled uid."""
        return self._get_proxy('scan').acquire_scan()
    
    @tango.server.command(dtype_out=str)
    def acquire_spectrum(self) -> str:
        """Acquire a spectrum using SPECTROSCOPY device settings; returns a DATA/Tiled uid."""
        return self._get_proxy('spectroscopy').acquire_spectrum()
    
    @tango.server.command(dtype_out=str)
    def approach(self) -> str:
        """Approach the tip to the surface; returns resulting approach state as JSON."""
        proxy = self._get_proxy('approach')
        proxy.approach()
        return json.dumps({'approached': bool(proxy.approached)})
    
    @tango.server.command(dtype_out=str)
    def retract(self) -> str:
        """Retract the tip from the surface; returns resulting approach state as JSON."""
        proxy = self._get_proxy('approach')
        proxy.retract()
        return json.dumps({'approached': bool(proxy.approached)})   

    @tango.server.command(dtype_out=str)
    def feedback_on(self) -> str:
        """Engage the Z feedback loop; returns resulting feedback state as JSON."""
        proxy = self._get_proxy('feedback')
        proxy.feedback_on()
        return json.dumps({'feedback_on': bool(proxy.feedback_on_bool),
                           'setpoint': float(proxy.setpoint)})
    
    @tango.server.command(dtype_out=str)
    def feedback_off(self) -> str:
        """Disengage the Z feedback loop; returns resulting feedback state as JSON."""
        proxy = self._get_proxy('feedback')
        proxy.feedback_off()
        return json.dumps({'feedback_on': bool(proxy.feedback_on_bool)})  
    
    @tango.server.command(dtype_in=tango.DevFloat, dtype_out=str)
    def set_setpoint(self, setpoint: float) -> str:
        """Set the feedback setpoint; returns the value read back as JSON."""
        proxy = self._get_proxy('feedback')
        proxy.setpoint = setpoint
        return json.dumps({'setpoint': float(proxy.setpoint)})
    
    @tango.server.command(dtype_in=tango.DevVarFloatArray, dtype_out=str)
    def move_stage(self, position) -> str:
        """Move the coarse stage; returns the final position as JSON."""
        proxy = self._get_proxy('stage')
        proxy.move_stage(position)
        return json.dumps({'position': list(proxy.position)})
    
    @tango.server.command(dtype_in=tango.DevVarFloatArray, dtype_out=str)
    def move_probe(self, position) -> str:
        """Move the probe within the scan frame; returns the final position as JSON."""
        proxy = self._get_proxy('scan')
        proxy.move_probe(position)
        return json.dumps({'probe_position': list(proxy.probe_position)})