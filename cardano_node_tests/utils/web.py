"""Functionality for publishing content on an internal web server."""

import logging
import pathlib as pl
import urllib.parse

from cardano_node_tests.utils import cluster_nodes

LOGGER = logging.getLogger(__name__)


def get_published_url(*, name: str = "") -> str:
    """Return the URL of the published file."""
    http_port = (
        cluster_nodes.get_cluster_type()
        .cluster_scripts.get_instance_ports(instance_num=cluster_nodes.get_instance_num())
        .webserver
    )

    url = urllib.parse.urljoin(
        base=f"http://localhost:{http_port}/p/", url=urllib.parse.quote(name)
    )
    return url


def publish(*, file_path: pl.Path, published_name: str = "", exist_ok: bool = False) -> str:
    """Publish file on an internal web server and return the URL."""
    fname = published_name or file_path.name
    if not fname:
        err = "Published name is empty."
        raise ValueError(err)

    web_dir = cluster_nodes.get_cluster_env().state_dir / "webserver" / "p"
    web_dir.mkdir(exist_ok=True)
    web_file = web_dir / fname
    if web_file.exists() and not exist_ok:
        err = f"File '{web_file}' already exists."
        raise FileExistsError(err)
    web_file.unlink(missing_ok=True)
    web_file.symlink_to(file_path.absolute())

    url = get_published_url(name=fname)
    return url


def create_file(*, name: str, data: str, exist_ok: bool = False) -> str:
    """Create file on an internal web server and return the URL."""
    web_dir = cluster_nodes.get_cluster_env().state_dir / "webserver" / "p"
    web_dir.mkdir(exist_ok=True)
    web_file = web_dir / name
    if web_file.exists() and not exist_ok:
        err = f"File '{web_file}' already exists."
        raise FileExistsError(err)
    web_file.unlink(missing_ok=True)
    web_file.write_text(data=data, encoding="utf-8")

    url = get_published_url(name=name)
    return url


def unpublish(*, url: str, missing_ok: bool = True) -> None:
    """Unpublish file from the internal web server."""
    encoded_url_parts = url.split("/p/")
    if len(encoded_url_parts) != 2:
        err = f"Invalid URL '{url}'."
        raise ValueError(err)
    encoded_fname = encoded_url_parts[1]
    fname = urllib.parse.unquote(encoded_fname)

    web_dir = cluster_nodes.get_cluster_env().state_dir / "webserver" / "p"
    web_file = web_dir / fname
    web_file.unlink(missing_ok=missing_ok)
