import glob
import os
import platform
import subprocess
import sys
from collections import defaultdict, namedtuple
from importlib import metadata
from typing import Union

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

    def __init__(self, iface, plugin_path=None):
        self.iface = iface
        self._defered_packages = []
        self.settings = QgsSettings()
        self.settings.beginGroup("QPIP")

        if plugin_path is None:
            self.plugins_path = os.path.join(
                QgsApplication.qgisSettingsDirPath(), "python", "plugins"
            )
        else:
            self.plugins_path = plugin_path
        self.prefix_path = os.path.join(
            QgsApplication.qgisSettingsDirPath().replace("/", os.path.sep),
            "python",
            "dependencies",
        )
        self.site_packages_path = os.path.join(self.prefix_path)
        self.bin_path = os.path.join(self.prefix_path, "bin")

        if self.site_packages_path not in sys.path:
            log(f"Adding {self.site_packages_path} to PYTHONPATH")
            sys.path.insert(0, self.site_packages_path)
            os.environ["PYTHONPATH"] = (
                self.site_packages_path + os.pathsep + os.environ.get("PYTHONPATH", "")
            )

        if self.bin_path not in os.environ["PATH"]:
            log(f"Adding {self.bin_path} to PATH")
            os.environ["PATH"] = self.bin_path + os.pathsep + os.environ["PATH"]

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
            dialog, run_gui = self.check_deps(additional_plugins=self._defered_packages)
            if run_gui:
                self.promt_install(dialog)
            self.save_settings(dialog)
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
                self.bin_path + os.pathsep, ""
            )
            os.environ["PATH"] = os.environ["PATH"].replace(
                self.bin_path + os.pathsep, ""
            )

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
                dialog, run_gui = self.check_deps(additional_plugins=[packageName])
                if run_gui:
                    self.promt_install(dialog)
                self.save_settings(dialog)
                self.start_packages([packageName])
                return True

    def check_deps(self, additional_plugins=[]) -> Union[MainDialog, bool]:
        """
        This checks dependencies for installed plugins and to-be installed plugins. If
        anything is missing, shows a GUI to install them.

        The function returns:
        - MainDialog, the QDialog object (without opening it)
        - A bool if the dialog needs to be opened or not
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
            if os.path.dirname(str(dist._path)) != self.site_packages_path:
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
        dialog = MainDialog(
            libs.values(), self._check_on_startup(), self._check_on_install()
        )
        return dialog, needs_gui

    def promt_install(self, dialog: MainDialog):
        """Promts the install dialog and ask the user what to install"""
        if dialog.exec_():
            reqs_to_uninstall = dialog.reqs_to_uninstall
            if reqs_to_uninstall:
                log(f"Will uninstall selected dependencies : {reqs_to_uninstall}")
                self.pip_uninstall_reqs(reqs_to_uninstall)

            reqs_to_install = dialog.reqs_to_install
            if reqs_to_install:
                log(f"Will install selected dependencies : {reqs_to_install}")
                self.pip_install_reqs(reqs_to_install)

    def save_settings(self, dialog):
        """Stores the settings values"""
        sys.path_importer_cache.clear()

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
                self.python_command(),
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
                self.python_command(),
                "-um",
                "pip",
                "install",
                *reqs_to_install,
                "--target",
                self.prefix_path,
            ],
            f"installing {len(reqs_to_install)} requirements",
        )

    def python_command(self):
        if os.path.exists(os.path.join(sys.prefix, "conda-meta")):  # Conda
            log("Attempt Conda install at 'python' shortcut")
            return "python"

        # python is normally found at sys.executable, but there is an issue on windows qgis so use 'python' instead: https://github.com/qgis/QGIS/issues/45646
        # 'python' doesnt seem to work, using this method instead
        if platform.system() == "Windows":  # Windows
            search_path = sys.prefix
            matches = glob.glob(os.path.join(search_path, "python*.exe"))
            for name in ("python.exe", "python3.exe"):
                for match in matches:
                    if os.path.basename(match) == name:
                        log(f"Attempt Windows install at {str(match)}")
                        return match
            path = sys.executable
            log(f"Attempt Windows install at {str(path)}")
            return path

        # Same bug on mac as windows: https://github.com/opengisch/qpip/issues/34#issuecomment-2995221985
        if platform.system() == "Darwin":  # Mac
            search_path = sys.prefix
            matches = glob.glob(os.path.join(search_path, "bin", "python*"))
            for name in ("python", "python3"):
                for match in matches:
                    if os.path.basename(match) == name:
                        log(f"Attempt MacOS install at {str(match)}")
                        return match
            path = sys.executable
            log(f"Attempt MacOS install at {str(path)}")
            return path

        else:  # Fallback attempt
            path = sys.executable
            log(f"Attempt fallback install at {str(path)}")
            return path

    def check(self):
        dialog, _ = self.check_deps()
        self.promt_install(dialog)
        self.save_settings(dialog)

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
