from __future__ import annotations

import os
import shutil

import pytest

os.environ["ENV_FILE"] = ".env.test"


@pytest.fixture(autouse=True)
def clean_upload_dir():
    path = "uploads_test"
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)
    yield
    if os.path.exists(path):
        shutil.rmtree(path)