import os
import platform
import subprocess
import sys
from collections import defaultdict, namedtuple
from importlib import metadata
from subprocess import (
    PIPE,
    STARTF_USESHOWWINDOW,
    STARTF_USESTDHANDLES,
    STARTUPINFO,
    STDOUT,
    SW_HIDE,
    Popen,
)

import pkg_resources
import qgis
from pkg_resources import DistributionNotFound, VersionConflict
from qgis.core import Qgis, QgsApplication, QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QProgressDialog

from .ui import MainDialog
from .utils import Lib, Req, icon, log, warn

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
        self.site_packages_path = os.path.join(self.prefix_path, "Lib", "site-packages")
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

        self.show_folder_action = QAction("Open current profile library folder")
        self.show_folder_action.triggered.connect(self.show_folder)
        self.iface.addPluginToMenu("QPIP", self.show_folder_action)

        self.toggle_startup_action = QAction("Check dependencies on startup")
        self.toggle_startup_action.setCheckable(True)
        self.toggle_startup_action.setChecked(self._is_check_on_startup_enabled())
        self.toggle_startup_action.toggled.connect(self.toggle_startup)
        self.iface.addPluginToMenu("QPIP", self.toggle_startup_action)

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
        self.iface.removePluginMenu("QPIP", self.toggle_startup_action)

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
            if not self._is_check_on_startup_enabled():
                # During QGIS startup, with initial loading disabled, we simply load the plugin
                log(f"Check disabled. Normal loading of {packageName}.")
                return self._original_loadPlugin(packageName)
            else:
                # During QGIS startup, with initial loading enabled, we defer loading
                log(f"GUI not ready. Deferring loading of {packageName}.")
                self._defered_packages.append(packageName)
                return False
        else:
            # QGIS ready, we install right away (probably a plugin that was just enabled)
            log(f"GUI ready. Insalling deps then loading {packageName}.")
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
            dialog = MainDialog(libs.values())
            if dialog.exec_():
                log("To uninstall:")
                log(str(dialog.deps_to_uninstall()))

                log("To install:")
                log(str(dialog.deps_to_install()))

                # deps_to_skip = dialog.deps_to_skip()
                deps_to_uninstall = dialog.deps_to_uninstall()
                deps_to_install = dialog.deps_to_install()

                # TODO: REENABLE SKIPS
                # for dep in deps_to_dontask:
                #     self.settings.setValue(
                #         f"skips/{dep.package}/{dep.requirement}", True
                #     )

                if deps_to_uninstall:
                    log(f"Will uninstall selected dependencies : {deps_to_uninstall}")
                    self.pip_uninstall_deps(deps_to_uninstall)
                    sys.path_importer_cache.clear()

                if deps_to_install:
                    log(f"Will install selected dependencies : {deps_to_install}")
                    self.pip_install_deps(deps_to_install)
                    sys.path_importer_cache.clear()

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

    def pip_uninstall_deps(self, deps_to_uninstall, extra_args=[]):
        """
        Unnstalls given deps with pip
        """
        # TODO: IMPLEMENT
        raise NotImplemented()

    def pip_install_deps(self, deps_to_install, extra_args=[]):
        """
        Installs given deps with pip
        """
        os.makedirs(self.prefix_path, exist_ok=True)
        reqs = [dep.requirement for dep in deps_to_install]
        log(f"Will pip install {reqs}")
        pip_args = [
            "python",
            "-um",
            "pip",
            "install",
            *reqs,
            "--prefix",
            self.prefix_path,
            *extra_args,
        ]

        progress_dlg = QProgressDialog(
            "Installing dependencies", "Abort", 0, 0, parent=self.iface.mainWindow()
        )
        progress_dlg.setWindowModality(Qt.WindowModal)
        progress_dlg.show()

        startupinfo = None
        if os.name == "nt":
            startupinfo = STARTUPINFO()
            startupinfo.dwFlags |= STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = SW_HIDE

        process = Popen(pip_args, stdout=PIPE, stderr=STDOUT, startupinfo=startupinfo)

        full_output = ""
        while True:
            QgsApplication.processEvents()
            try:
                # FIXME : this doesn't seem to timeout
                out, _ = process.communicate(timeout=0.1)
                output = out.decode(errors="replace").strip()
                full_output += output
                if output:
                    progress_dlg.setLabelText(output)
                    log(output)
            except subprocess.TimeoutExpired:
                pass

            if progress_dlg.wasCanceled():
                process.kill()
            if process.poll() is not None:
                break

        progress_dlg.close()

        if process.returncode != 0:
            warn(f"Installation failed.")
            message = QMessageBox(
                QMessageBox.Warning,
                "Installation failed",
                f"Installation of dependecies failed (code {process.returncode}).\nSee logs for more information.",
                parent=self.iface.mainWindow(),
            )
            message.setDetailedText(full_output)
            message.exec_()
        else:
            log("Installation succeeded.")
            self.iface.messageBar().pushMessage(
                "Success",
                f"Installed {len(deps_to_install)} requirements",
                level=Qgis.Success,
            )

    def check(self):
        self.check_deps_and_prompt_install(force_gui=True)

    def toggle_startup(self, toggled):
        # seems QgsSettings doesn't deal well with bools !!
        self.settings.setValue("check_on_startup", "yes" if toggled else "no")

    def show_folder(self):
        if platform.system() == "Windows":
            os.startfile(self.prefix_path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", self.prefix_path])
        else:
            subprocess.Popen(["xdg-open", self.prefix_path])

    def _is_check_on_startup_enabled(self):
        return self.settings.value("check_on_startup", "yes") == "yes"

    def _is_qgis_loaded(self):
        return self.iface.mainWindow().isVisible()
