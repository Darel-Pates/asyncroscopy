# DATA acquisition workflow

See more at https://github.com/bluesky/tiled.

`ThermoMicroscope` now saves real AutoScript acquisitions on the microscope
side and returns a JSON descriptor string through Tango. `asyncroscopy/software/DATA.py`
is the Tango data device for reading those descriptors back through the Tiled
HTTP server.

The preferred save format is `.emd`:

```python
EmdFile.create("stem_image.emd", EmdStemFeature([adorned_image])).close()
```

AutoScript requires complete image metadata for EMD export. If EMD creation
fails, asyncroscopy falls back to `adorned.save("...tiff")`, because TIFF is
the format supported by `AdornedImage.save()` that preserves metadata.

## Notebook setup

Connect to the DATA Tango device once at the beginning of a workflow. The
`save_path` directory should be visible to the Tiled HTTP server, and the
microscope device should have `data_device_address` set to this Tango device.

```python
import json
import tango
from getpass import getpass

data = tango.DeviceProxy("asyncroscopy/data/default")
data.host = "10.46.217.241"
data.port = 9091
data.save_path = "/path/served/by/tiled"
data.root_path = ""
data.set_api_key(getpass("Enter your Tiled API key: "))
```

Acquire as usual, but treat the return value as a descriptor:

```python
descriptor = mic.get_scanned_image()
```

Use the DATA device to inspect recent files or resolve data through Tiled:

```python
recent = json.loads(data.get_recent())
array = json.loads(data.get_data(descriptor))
```

The descriptor includes both extension-stripped and extension-preserving Tiled
path candidates because Tiled directory adapters can be configured either way.

## Server Roles

There are two data-related servers:

- `asyncroscopy/data/default` is the DATA Tango device server. It belongs to asyncroscopy and bridges notebooks or microscope devices to Tiled.
- `http://10.46.217.241:9091` is the Tiled HTTP data server. It indexes and serves files.

The DATA device is started with the other Tango devices. The Tiled HTTP server
is started separately and must already be reachable.

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
