import os
import pathlib as pl

if not os.environ.get("CARDANO_NODE_SOCKET_PATH"):
    os.environ["CARDANO_NODE_SOCKET_PATH"] = "/nonexistent/state-cluster/relay1.socket"
    mockdir = pl.Path(__file__).parent / "mocks"
    os.environ["PATH"] = f"{mockdir}:{os.environ['PATH']}"
