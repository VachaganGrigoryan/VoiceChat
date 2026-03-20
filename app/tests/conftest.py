import os
import shutil
import stat
import pytest


def _on_rm_error(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_upload_dir():
    path = "uploads_test"

    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=False, onerror=_on_rm_error)

    os.makedirs(path, exist_ok=True)

    yield

    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=False, onerror=_on_rm_error)