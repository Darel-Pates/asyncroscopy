# Tiled acquisition workflow

See more at https://github.com/bluesky/tiled.

`ThermoMicroscope` now saves real AutoScript acquisitions on the microscope
side and returns a JSON descriptor string through Tango. `asyncroscopy/Tiled.py`
is the notebook-facing Tango device for reading those descriptors back from the
Tiled server.

The preferred save format is `.emd`:

```python
EmdFile.create("stem_image.emd", EmdStemFeature([adorned_image])).close()
```

AutoScript requires complete image metadata for EMD export. If EMD creation
fails, asyncroscopy falls back to `adorned.save("...tiff")`, because TIFF is
the format supported by `AdornedImage.save()` that preserves metadata.

## Notebook setup

Connect to the Tiled Tango device once at the beginning of a workflow. The
`save_path` directory should be visible to the Tiled server, and the microscope
device should have `tiled_device_address` set to this Tango device.

```python
import json
import tango
from getpass import getpass

tiled = tango.DeviceProxy("asyncroscopy/tiled/default")
tiled.host = "10.46.217.241"
tiled.port = 9091
tiled.save_path = "/path/served/by/tiled"
tiled.root_path = ""  # optional path prefix inside Tiled
tiled.set_api_key(getpass("Enter your Tiled API key: "))
```

Acquire as usual, but treat the return value as a descriptor:

```python
descriptor = mic.get_scanned_image()
```

Use the Tiled device to inspect recent files or resolve data through Tiled:

```python
recent = json.loads(tiled.get_recent())
data = json.loads(tiled.get_data(descriptor))
```

The descriptor includes both extension-stripped and extension-preserving Tiled
path candidates because Tiled directory adapters can be configured either way.

## Direct Tiled access

We currently access the server in the notebook like this:

import os
from tiled.client import from_uri
from getpass import getpass

"note: the key is 'secret'"
os.environ["TILED_API_KEY"] = getpass("Enter your Tiled API key: ")

client = from_uri(
    "http://10.46.217.241:9091",
    api_key=os.environ["TILED_API_KEY"],
)

list(client) # should print out some folders and files
