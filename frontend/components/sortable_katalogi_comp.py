import socket
import threading
import http.server
from pathlib import Path
import streamlit.components.v1 as stc

_SRC_DIR = Path(__file__).parent / "sortable_katalogi"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _start_server(directory: str, port: int) -> None:
    class _H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=directory, **kw)

        def log_message(self, *a):
            pass

    with http.server.HTTPServer(("localhost", port), _H) as srv:
        srv.serve_forever()


_PORT = _free_port()
threading.Thread(
    target=_start_server, args=(str(_SRC_DIR), _PORT), daemon=True
).start()

sortable_katalogi = stc.declare_component(
    "sortable_katalogi",
    url=f"http://localhost:{_PORT}/",
)
