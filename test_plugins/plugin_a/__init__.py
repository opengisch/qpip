from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pkg_resources import parse_requirements
from qgis.PyQt.QtWidgets import QAction, QMessageBox


def classFactory(iface):
    return Plugin(iface)


class Plugin:
    def __init__(self, iface):
        self.iface = iface

    def initGui(self):
        self.action = QAction("qpip_test_plugin_a")
        self.action.triggered.connect(self.do)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)

    def reqs(self):
        req_path = Path(__file__).parent / "requirements.txt"
        for req in parse_requirements(req_path.read_text()):
            try:
                installed = version(req.key)
            except PackageNotFoundError:
                installed = "not installed"
            yield f"{req} [{installed}]"

    def do(self):
        QMessageBox.information(
            self.iface.mainWindow(),
            "Plugin A requirements",
            "Dependencies:\n" + "\n".join(self.reqs()),
        )
