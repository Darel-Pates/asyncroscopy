# Ceos_server.py

"""
the real thing.
not async yet (socket)
this one is really just a translator for the real CEOS server.
"""

import logging
import json
import traceback
import socket
from twisted.internet import reactor,defer, protocol
from asyncroscopy.servers.protocols.execution_protocol import ExecutionProtocol
from asyncroscopy.servers.protocols.utils import package_message, unpackage_message

logging.basicConfig()
log = logging.getLogger('CEOS_acquisition')
log.setLevel(logging.INFO)

# FACTORY — holds shared state (persistent across all connections)
class CeosFactory(protocol.Factory):
    def __init__(self):
        # persistent states for all protocol instances
        self.microscope = None
        self.detectors = {}
        self.status = "Offline"

    def buildProtocol(self, addr):
        """Create a new protocol instance and attach the factory (shared state)."""
        proto = CeosProtocol()
        proto.factory = self
        return proto


# PROTOCOL — handles per-connection command execution
class CeosProtocol(ExecutionProtocol):
    def __init__(self):
        super().__init__()
        self.host = "10.46.217.241"
        self.port = 9092
        self._nextMessageID = 1
        self._pendingCommands = {}

    # Override stringReceived for special case of Ceos commands
    def stringReceived(self, data: bytes):
        msg = data.decode().strip()
        print(f"[Exec] Received: {msg}")
        parts = msg.split()
        cmd, *args_parts = parts

        args_dict = dict(arg.split('=', 1) for arg in args_parts if '=' in arg)
        payload = {
            "jsonrpc":"2.0",
            "id":self._nextMessageID,
            "method":cmd,
            "params":args_dict
        }
        print("[Exec] Sending payload to CEOS:", payload)
        # Serialize dict to JSON bytes
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        netstring = f"{len(payload_bytes)}:".encode("ascii") + payload_bytes + b","
        print("[Exec] Netstring to send:", netstring)

        self._nextMessageID += 1

        with socket.create_connection((self.host, self.port), timeout=3000) as sock:
            sock.sendall(netstring)

            # Read until we hit a complete netstring (ends with b",")
            buffer = b""
            while not buffer.endswith(b","):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
        msg = buffer.decode()
        print("[Exec] Received netstring from CEOS:", msg)
        self.sendString(package_message(msg))

if __name__ == "__main__":
    port = 9003
    print(f"[Ceos] Server running on port {port}...")
    reactor.listenTCP(port, CeosFactory())
    reactor.run()