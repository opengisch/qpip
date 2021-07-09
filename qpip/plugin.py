import os.path

from qgis.core import QgsMessageLog, QgsProviderMetadata, QgsProviderRegistry
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import Provider

QgsMessageLog.logMessage("loading qdmtk file", "QDMTK")


class Plugin:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""

        # Register our provider
        metadata = QgsProviderMetadata(
            Provider.providerKey(), Provider.description(), Provider.createProvider
        )
        QgsProviderRegistry.instance().registerProvider(metadata)

        # Add toolbar
        self.toolbar = self.iface.addToolBar("qpip")

        self.main_action = QAction(
            QIcon(os.path.join(self.plugin_dir, "icon.svg")),
            "Run qpip",
            self.toolbar,
        )
        # self.main_action.triggered.connect(self.do_stuff)
        self.toolbar.addAction(self.main_action)

    def unload(self):
        self.iface.mainWindow().removeToolBar(self.toolbar)
