import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "bbp.main",
        "bbp.consumer",
        "bbp.dns_resolver",
        "bbp.publisher_pool",
        "bbp.tool_actor",
        "bbp.tool_pool",
        "bbp.tools.httpx",
        "bbp.tools.katana",
        "bbp.tools.nmap",
        "bbp.tools.subfinder",
    ],
)
def test_service_modules_import(module_name):
    assert importlib.import_module(module_name)
