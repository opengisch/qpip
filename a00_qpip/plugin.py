import os
import platform
import subprocess
import sys
from collections import defaultdict, namedtuple
from importlib import metadata

import pkg_resources
import qgis
from pkg_resources import DistributionNotFound, VersionConflict
from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtWidgets import QAction

from .ui import MainDialog
from .utils import Lib, Req, icon, log, run_cmd

MissingDep = namedtuple("MissingDep", ["package", "requirement", "state"])


class Plugin:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self._defered_packages = []
        self.settings = QgsSettings()
        self.settings.beginGroup("QPIP")

        self.plugins_path = os.path.join(
            QgsApplication.qgisSettingsDirPath(), "python", "plugins"
        )
        self.prefix_path = os.path.join(
            QgsApplication.qgisSettingsDirPath().replace("/", os.path.sep),
            "python",
            "dependencies",
        )
        self.site_packages_path = self.prefix_path
        self.bin_path = os.path.join(self.prefix_path, "Scripts")

        if self.site_packages_path not in sys.path:
            log(f"Adding {self.site_packages_path} to PYTHONPATH")
            sys.path.insert(0, self.site_packages_path)
            os.environ["PYTHONPATH"] = (
                self.site_packages_path + ";" + os.environ.get("PYTHONPATH", "")
            )

        if self.bin_path not in os.environ["PATH"]:
            log(f"Adding {self.bin_path} to PATH")
            os.environ["PATH"] = self.bin_path + ";" + os.environ["PATH"]

        sys.path_importer_cache.clear()

        # Monkey patch qgis.utils
        log("Applying monkey patch to qgis.utils")
        self._original_loadPlugin = qgis.utils.loadPlugin
        qgis.utils.loadPlugin = self.patched_load_plugin

        self.iface.initializationCompleted.connect(self.initComplete)

    def initGui(self):
        self.check_action = QAction(icon("qpip.svg"), "Run dependencies check now")
        self.check_action.triggered.connect(self.check)
        self.iface.addToolBarIcon(self.check_action)
        self.iface.addPluginToMenu("QPIP", self.check_action)

        self.show_folder_action = QAction("Show library folder in explorer")
        self.show_folder_action.triggered.connect(self.show_folder)
        self.iface.addPluginToMenu("QPIP", self.show_folder_action)

    def initComplete(self):
        if self._defered_packages:
            log(f"Initialization complete. Loading deferred packages")
            self.check_deps_and_prompt_install(
                additional_plugins=self._defered_packages
            )
            self.start_packages(self._defered_packages)
        self._defered_packages = []

    def unload(self):
        self.iface.removePluginMenu("QPIP", self.check_action)
        self.iface.removeToolBarIcon(self.check_action)
        self.iface.removePluginMenu("QPIP", self.show_folder_action)

        # Remove monkey patch
        log("Unapplying monkey patch to qgis.utils")
        qgis.utils.loadPlugin = self._original_loadPlugin

        # Remove path alterations
        if self.site_packages_path in sys.path:
            sys.path.remove(self.site_packages_path)
            os.environ["PYTHONPATH"] = os.environ["PYTHONPATH"].replace(
                self.bin_path + ";", ""
            )
            os.environ["PATH"] = os.environ["PATH"].replace(self.bin_path + ";", "")

    def patched_load_plugin(self, packageName):
        """
        This replaces qgis.utils.loadPlugin
        """
        if not self._is_qgis_loaded():
            # During QGIS startup
            log(f"Loading {packageName} (GUI is no yet ready).")
            if not self._check_on_startup():
                # With initial loading disabled, we simply load the plugin
                log(f"Check on startup disabled. Normal loading of {packageName}.")
                return self._original_loadPlugin(packageName)
            else:
                # With initial loading enabled, we defer loading
                log(f"Check on startup enabled, we defer loading of {packageName}.")
                self._defered_packages.append(packageName)
                return False
        else:
            # QGIS ready, a plugin probably was just enabled in the manager
            log(f"Loading {packageName} (GUI is ready).")
            if not self._check_on_install():
                # With loading on install disabled, we simply load the plugin
                log(f"Check on install disabled. Normal loading of {packageName}.")
                return self._original_loadPlugin(packageName)
            else:
                log(f"Check on install enabled, we check {packageName}.")
                self.check_deps_and_prompt_install(additional_plugins=[packageName])
                self.start_packages([packageName])
                return True

    def check_deps_and_prompt_install(self, additional_plugins=[], force_gui=False):
        """
        This checks dependencies for installed plugins and to-be installed plugins. If
        anything is missing, shows a GUI to install them.
        """

        plugin_names = [*qgis.utils.active_plugins, *additional_plugins]

        log(f"Checking deps for the following plugins: {plugin_names}")

        # This will hold all dependencies
        libs = defaultdict(Lib)

        # Loading installed libs
        for dist in metadata.distributions():
            name = dist.metadata["Name"]
            libs[name].name = name
            libs[name].installed_dist = dist
            if os.path.dirname(dist._path) != self.site_packages_path:
                libs[name].qpip = False

        # Checking requirements of all plugins
        needs_gui = False
        for plugin_name in plugin_names:
            # If requirements.txt is present, we see if we can load it
            requirements_path = os.path.join(
                self.plugins_path, plugin_name, "requirements.txt"
            )
            if os.path.isfile(requirements_path):
                log(f"Loading requirements for {plugin_name}")
                with open(requirements_path, "r") as f:
                    requirements = pkg_resources.parse_requirements(f)
                    working_set = pkg_resources.WorkingSet()
                    for requirement in requirements:
                        try:
                            working_set.require(str(requirement))
                            error = None
                        except (VersionConflict, DistributionNotFound) as e:
                            needs_gui = True
                            error = e
                        req = Req(plugin_name, str(requirement), error)
                        libs[requirement.key].name = requirement.key
                        libs[requirement.key].required_by.append(req)

        if force_gui or needs_gui:
            dialog = MainDialog(
                libs.values(), self._check_on_startup(), self._check_on_install()
            )
            if dialog.exec_():
                reqs_to_uninstall = dialog.reqs_to_uninstall
                if reqs_to_uninstall:
                    log(f"Will uninstall selected dependencies : {reqs_to_uninstall}")
                    self.pip_uninstall_reqs(reqs_to_uninstall)

                reqs_to_install = dialog.reqs_to_install
                if reqs_to_install:
                    log(f"Will install selected dependencies : {reqs_to_install}")
                    self.pip_install_reqs(reqs_to_install)

                sys.path_importer_cache.clear()

            # Save these even if the dialog was closed
            self.settings.setValue(
                "check_on_startup", "yes" if dialog.check_on_startup else "no"
            )
            self.settings.setValue(
                "check_on_install", "yes" if dialog.check_on_install else "no"
            )

    def start_packages(self, packageNames):
        """
        This loads and starts all packages.

        It tries to match implementation of QgsPluginRegistry::loadPythonPlugin (including watchdog).
        """

        for packageName in packageNames:
            log(f"Proceeding to load {packageName}")
            could_load = self._original_loadPlugin(packageName)

            if could_load:
                # When called deferred, we also need to start the plugin. This matches implementation
                # of QgsPluginRegistry::loadPythonPlugin
                could_start = qgis.utils.startPlugin(packageName)
                if could_start:
                    QgsSettings().setValue("/PythonPlugins/" + packageName, True)
                    QgsSettings().remove("/PythonPlugins/watchDog/" + packageName)

    def pip_uninstall_reqs(self, reqs_to_uninstall, extra_args=[]):
        """
        Unnstalls given deps with pip
        """
        log(f"Will pip uninstall {reqs_to_uninstall}")

        run_cmd(
            [
                sys.executable,
                "-um",
                "pip",
                "uninstall",
                "-y",
                *reqs_to_uninstall,
            ],
            f"uninstalling {len(reqs_to_uninstall)} requirements",
        )

    def pip_install_reqs(self, reqs_to_install):
        """
        Installs given reqs with pip
        """
        os.makedirs(self.prefix_path, exist_ok=True)
        log(f"Will pip install {reqs_to_install}")

        run_cmd(
            [
                sys.executable,
                "-um",
                "pip",
                "install",
                *reqs_to_install,
                "--target",
                self.prefix_path,
            ],
            f"installing {len(reqs_to_install)} requirements",
        )

    def check(self):
        self.check_deps_and_prompt_install(force_gui=True)

    def show_folder(self):
        if platform.system() == "Windows":
            os.startfile(self.prefix_path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", self.prefix_path])
        else:
            subprocess.Popen(["xdg-open", self.prefix_path])

    def _check_on_startup(self):
        return self.settings.value("check_on_startup", "no") == "yes"

    def _check_on_install(self):
        return self.settings.value("check_on_install", "yes") == "yes"

    def _is_qgis_loaded(self):
        return self.iface.mainWindow().isVisible()
