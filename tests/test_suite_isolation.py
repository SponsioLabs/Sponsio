"""The session isolation in ``conftest.py`` holds.

Nothing here moves HOME or patches the network: these call the credential
and cloud paths the way a careless test would, and pass only because the
conftest isolated the session first.
"""

from __future__ import annotations

import os
import pwd
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sponsio.cloud.client import CloudClient, CloudError, credentials_path, read_api_key

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_home_is_not_the_real_home():
    assert Path.home() != REAL_HOME
    assert not credentials_path().is_relative_to(REAL_HOME / ".sponsio")


def test_no_cloud_variable_reaches_a_test():
    assert "SPONSIO_API_KEY" not in os.environ
    assert "SPONSIO_API_URL" not in os.environ


def test_the_real_credentials_file_is_not_read():
    assert read_api_key() is None
    assert CloudClient().configured is False


def test_opening_the_real_credentials_file_is_refused():
    # Refused before the filesystem is asked, so this holds whether or not
    # the file exists on this machine.
    with pytest.raises(PermissionError, match="real home"):
        (REAL_HOME / ".sponsio" / "credentials").read_text()


def test_a_cloud_request_never_leaves_the_machine():
    # A key is supplied, so only the network guard stands between this call
    # and the production endpoint.
    client = CloudClient(api_key="sk-test-not-a-key", retries=0)
    assert client.url == "https://app.sponsio.dev"
    with pytest.raises(CloudError, match="network disabled in tests"):
        client.whoami()


def test_a_plain_urlopen_is_refused_too():
    with pytest.raises(urllib.error.URLError, match="network disabled in tests"):
        urllib.request.urlopen("https://app.sponsio.dev/v1/whoami", timeout=2)


def test_loopback_still_works():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()
    finally:
        server.close()
