"""Send a fixed left click to the disposable VM's private QMP socket."""

import json
import socket
import sys
import time

with socket.socket(socket.AF_UNIX) as connection:
    connection.settimeout(5)
    connection.connect(sys.argv[1])
    stream = connection.makefile("rwb")
    assert "QMP" in json.loads(stream.readline())

    def execute(name, arguments=None):
        request = {"execute": name}
        if arguments is not None:
            request["arguments"] = arguments
        stream.write(json.dumps(request).encode() + b"\n")
        stream.flush()
        while True:
            response = json.loads(stream.readline())
            if "event" not in response:
                assert "return" in response, response
                return

    execute("qmp_capabilities")
    for down in (True, False):
        execute(
            "input-send-event",
            {"events": [{"type": "btn", "data": {"button": "left", "down": down}}]},
        )
        time.sleep(0.15)
