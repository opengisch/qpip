import os

import pytest
from pytest_qgis import qgis_iface

from PyQt5.QtCore import QSettings, QDate

from a00_qpip.plugin import Plugin


class initializationCompleted:
    def connect(self):
        pass


def popWidget():
    return True


THIS_DIR = os.path.dirname(__file__)


@pytest.fixture()
def plugin(qgis_iface):
    qgis_iface.initializationCompleted = initializationCompleted
    qgis_iface.messageBar().popWidget = popWidget
    plugin = Plugin(qgis_iface, ".")
    yield plugin


def test_plugin_a(plugin: Plugin):
    plugin_a = os.path.join(THIS_DIR, "..", "test_plugins", "plugin_a")
    dialog, needs_gui = plugin.check_deps([plugin_a])
    libs = dialog.reqs_to_install
    assert len(libs) == 2
    assert libs[0] == "cowsay==4.0"
    assert needs_gui


def test_plugin_b(plugin: Plugin):
    plugin_b = os.path.join(THIS_DIR, "..", "test_plugins", "plugin_b")
    dialog, needs_gui = plugin.check_deps([plugin_b])
    libs = dialog.reqs_to_install
    assert len(libs) == 2
    assert libs[0] == "cowsay==5.0"
    assert needs_gui
