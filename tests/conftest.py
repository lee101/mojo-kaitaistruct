import importlib.metadata
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))


@pytest.fixture(scope="session")
def upstream():
    path = importlib.metadata.distribution("kaitaistruct").locate_file("kaitaistruct.py")
    spec = importlib.util.spec_from_file_location("_kaitaistruct_upstream", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
