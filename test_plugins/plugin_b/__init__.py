from qgis.PyQt.QtWidgets import QAction


def classFactory(iface):
    return Plugin(iface)


class Plugin:
    def __init__(self, iface):
        self.iface = iface

    def initGui(self):
        self.action = QAction("plugin_b")
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
