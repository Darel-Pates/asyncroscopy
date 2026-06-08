"""
SPM_SCAN hardware settings.
This device holds scan acquisition settings.
It does NOT talk to API directly — the SPM_Microscope device
reads these attributes via DeviceProxy before acquiring.
"""

from tango import AttrWriteType, DevState
from tango.server import Device, attribute