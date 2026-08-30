import sys

import pytest
from qgis.PyQt.QtCore import QTimer

from a00_qpip import utils
from a00_qpip.install_progress import PipInstallProgressDialog
from a00_qpip.pip_progress import ProgressUpdate


def make_dialog(qgis_app):
    return PipInstallProgressDialog(
        ["python", "-m", "pip", "--version"],
        ["numpy>=2", "scipy>=1"],
        "testing progress",
        lambda _message: None,
    )


def test_download_bytes_are_scaled_to_percentage(qgis_app):
    dialog = make_dialog(qgis_app)
    dialog._apply_update(
        ProgressUpdate("numpy", "Downloading", 3_000_000_000, 4_000_000_000)
    )

    _row, progress = dialog.rows["numpy"]
    assert progress.maximum() == 100
    assert progress.value() == 75


def test_failure_marks_only_unfinished_dependencies(qgis_app):
    dialog = make_dialog(qgis_app)
    dialog._apply_update(ProgressUpdate("numpy", "Completed", 1, 1))
    dialog._apply_update(ProgressUpdate("scipy", "Installing"))

    dialog._mark_unfinished("Error")

    numpy_row, _progress = dialog.rows["numpy"]
    scipy_row, _progress = dialog.rows["scipy"]
    assert dialog.table.item(numpy_row, 1).text() == "Completed"
    assert dialog.table.item(scipy_row, 1).text() == "Error"


def test_abort_kills_process_and_marks_dependency_cancelled(qgis_app):
    dialog = PipInstallProgressDialog(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        ["numpy>=2"],
        "testing cancellation",
        lambda _message: None,
    )
    QTimer.singleShot(100, dialog._abort)

    exit_code, cancelled, _output = dialog.execute()

    row, _progress = dialog.rows["numpy"]
    assert exit_code != 0
    assert cancelled
    assert dialog.table.item(row, 1).text() == "Cancelled"


@pytest.mark.parametrize(
    ("requirements", "expected_description"),
    [
        (["numpy>=2"], "installing 1 requirement"),
        (["numpy>=2", "scipy>=1"], "installing 2 requirements"),
    ],
)
def test_run_pip_install_builds_description_from_requirements(
    monkeypatch, requirements, expected_description
):
    captured = {}

    class FakeDialog:
        def __init__(self, _args, _requirements, description, **_kwargs):
            captured["description"] = description

        def execute(self):
            return 0, False, ""

    class FakeMessageBar:
        def pushMessage(self, *_args, **_kwargs):
            pass

    class FakeIface:
        def mainWindow(self):
            return None

        def messageBar(self):
            return FakeMessageBar()

    monkeypatch.setattr(utils, "PipInstallProgressDialog", FakeDialog)
    monkeypatch.setattr(utils, "iface", FakeIface())

    assert utils.run_pip_install(["python", "-m", "pip"], requirements)
    assert captured["description"] == expected_description
