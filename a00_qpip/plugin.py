import os
import platform
import shutil
import subprocess
import sys
from collections import defaultdict, namedtuple
from importlib import metadata
from pathlib import Path
from typing import Union

import qgis
from packaging.markers import default_environment
from packaging.requirements import Requirement
from pyplugin_installer import installer
from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtWidgets import QAction, QApplication

from .ui import MainDialog
from .utils import (
    DistributionNotFound,
    Lib,
    Req,
    VersionConflict,
    icon,
    log,
    run_cmd,
    warn,
)

MissingDep = namedtuple("MissingDep", ["package", "requirement", "state"])


class Plugin:
    """QGIS Plugin Implementation."""

    def __init__(self, iface, plugin_path=None):
        self.iface = iface
        self._defered_packages = []
        self.settings = QgsSettings()
        self.settings.beginGroup("QPIP")

        self.prefix_path = (
            Path(QgsApplication.qgisSettingsDirPath()) / "python" / "dependencies"
        )
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        self.prefix_path = self.base_deps_path / py_ver
        self.site_packages_path = self.prefix_path
        self.bin_path = self.prefix_path / "bin"

        self._migrate_old_dependencies()

        if self.site_packages_path not in sys.path:
            log(f"Adding {self.site_packages_path} to PYTHONPATH")
            sys.path.insert(0, str(self.site_packages_path))
            os.environ["PYTHONPATH"] = (
                str(self.site_packages_path)
                + os.pathsep
                + os.environ.get("PYTHONPATH", "")
            )

        if str(self.bin_path) not in os.environ["PATH"]:
            log(f"Adding {self.bin_path} to PATH")
            os.environ["PATH"] = str(self.bin_path) + os.pathsep + os.environ["PATH"]

        sys.path_importer_cache.clear()

        # Monkey patch qgis.utils and installer
        log("Applying monkey patch to qgis.utils and installer")
        self._original_loadPlugin = qgis.utils.loadPlugin
        qgis.utils.loadPlugin = self.patched_load_plugin
        installer.loadPlugin = self.patched_load_plugin

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
                self.prompt_install(dialog)
            self.save_settings(dialog)
            self.start_packages(self._defered_packages)
        self._defered_packages = []

    def unload(self):
        self.iface.removePluginMenu("QPIP", self.check_action)
        self.iface.removeToolBarIcon(self.check_action)
        self.iface.removePluginMenu("QPIP", self.show_folder_action)

        # Remove monkey patch
        log("Unapplying monkey patch to qgis.utils and installer")
        qgis.utils.loadPlugin = self._original_loadPlugin
        installer.loadPlugin = self._original_loadPlugin

        # Remove path alterations
        if str(self.site_packages_path) in sys.path:
            sys.path.remove(str(self.site_packages_path))
            os.environ["PYTHONPATH"] = os.environ["PYTHONPATH"].replace(
                str(self.site_packages_path) + os.pathsep, ""
            )
            os.environ["PATH"] = os.environ["PATH"].replace(
                str(self.bin_path) + os.pathsep, ""
            )

    def patched_load_plugin(self, packageName):
        """
        This replaces qgis.utils.loadPlugin
        """
        res = False

        # Get override cursor if any, to restore it later
        cursor_shape = None
        cursor = QApplication.overrideCursor()
        if cursor:
            cursor_shape = cursor.shape()
            QApplication.restoreOverrideCursor()

        if not self._is_qgis_loaded():
            # During QGIS startup
            log(f"Loading {packageName} (GUI is no yet ready).")
            if not self._check_on_startup():
                # With initial loading disabled, we simply load the plugin
                log(f"Check on startup disabled. Normal loading of {packageName}.")
                res = self._original_loadPlugin(packageName)
            else:
                # With initial loading enabled, we defer loading
                log(f"Check on startup enabled, we defer loading of {packageName}.")
                self._defered_packages.append(packageName)
                res = False
        else:
            # QGIS ready, a plugin probably was just enabled in the manager
            log(f"Loading {packageName} (GUI is ready).")
            if not self._check_on_install():
                # With loading on install disabled, we simply load the plugin
                log(f"Check on install disabled. Normal loading of {packageName}.")
                res = self._original_loadPlugin(packageName)
            else:
                log(f"Check on install enabled, we check {packageName}.")
                dialog, run_gui = self.check_deps(additional_plugins=[packageName])
                if run_gui:
                    self.prompt_install(dialog)
                self.save_settings(dialog)
                self.start_packages([packageName])
                res = True

        # Restore original cursor
        if cursor_shape:
            QApplication.setOverrideCursor(cursor_shape)

        return res

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
            if Path(str(dist._path)).parent != self.site_packages_path:
                libs[name].qpip = False

        # Checking requirements of all plugins
        
        # fetch plugin information first
        import pyplugin_installer
        pyplugin_installer.instance().fetchAvailablePlugins(False)
        all_plugin_metadata = pyplugin_installer.installer_data.plugins.all()
        
        # get the qgis path to ignore core plugins
        qgis_path = Path(QgsApplication.prefixPath()).resolve()

        needs_gui = False
        env = default_environment()
        for plugin_name in plugin_names:
            # Get metadata for a specific plugin by its ID (folder name) as a dictionary
            plugin_metadata = all_plugin_metadata.get(plugin_name)
            if not plugin_metadata:
                log(f"Could not find metadata for plugin {plugin_name}, skipping.")
                continue

            # get the plugins installation folder
            plug_path = Path(plugin_metadata.get('library')).resolve()
            
            # check it against the Qgis path to see if its core
            if plug_path.is_relative_to(qgis_path) :
                continue

            # If requirements.txt is present, we see if we can load it
            requirements_path = plug_path.joinpath('requirements.txt')
            if requirements_path.is_file():
                log(f"Loading requirements for {plugin_name}")
                with open(requirements_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or line.startswith("-"):
                            continue
                        requirement = Requirement(line)
                        if requirement.marker and not requirement.marker.evaluate(env):
                            continue

                        try:
                            dist = metadata.distribution(requirement.name)
                            version = dist.metadata["Version"]
                            if (
                                requirement.specifier
                                and not requirement.specifier.contains(version)
                            ):
                                error = VersionConflict(
                                    f"{requirement.name} {version} does not satisfy {requirement.specifier}"
                                )
                                needs_gui = True
                            else:
                                error = None
                        except metadata.PackageNotFoundError:
                            error = DistributionNotFound(
                                f"{requirement.name} is not installed"
                            )
                            needs_gui = True
                        req = Req(plugin_name, str(requirement), error)
                        libs[requirement.name].name = requirement.name
                        libs[requirement.name].required_by.append(req)
        dialog = MainDialog(
            libs.values(), self._check_on_startup(), self._check_on_install()
        )
        return dialog, needs_gui

    def prompt_install(self, dialog: MainDialog):
        """Prompts the installation dialog and ask the user what to install"""
        if dialog.exec():
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
        self.prefix_path.mkdir(parents=True, exist_ok=True)
        log(f"Will pip install {reqs_to_install}")

        run_cmd(
            [
                self.python_command(),
                "-um",
                "pip",
                "install",
                *reqs_to_install,
                "--target",
                str(self.prefix_path),
            ],
            f"installing {len(reqs_to_install)} requirements",
        )

    def python_command(self):
        if (Path(sys.prefix) / "conda-meta").exists():  # Conda
            log("Attempt Conda install at 'python' shortcut")
            return "python"

        # python is normally found at sys.executable, but there is an issue on windows qgis so use 'python' instead: https://github.com/qgis/QGIS/issues/45646
        # 'python' doesnt seem to work, using this method instead
        if platform.system() == "Windows":  # Windows
            base_path = Path(sys.prefix)
            for file in ["python.exe", "python3.exe"]:
                path = base_path / file
                if path.is_file():
                    log(f"Attempt Windows install at {str(path)}")
                    return str(path)
            path = sys.executable
            log(f"Attempt Windows install at {str(path)}")
            return path

        # Same bug on mac as windows: https://github.com/opengisch/qpip/issues/34#issuecomment-2995221985
        if platform.system() == "Darwin":  # Mac
            base_paths = [
                Path(sys.prefix),
                Path(sys.prefix) / "bin",
                Path(sys.executable).parent,
            ]
            for base_path in base_paths:
                for file in ["python", "python3"]:
                    path = base_path / file
                    if path.is_file():
                        log(f"Attempt MacOS install at {str(path)}")
                        return str(path)
            path = sys.executable
            log(f"Attempt MacOS install at {str(path)}")
            return path

        else:  # Fallback attempt
            path = sys.executable
            log(f"Attempt fallback install at {str(path)}")
            return path

    def check(self):
        dialog, _ = self.check_deps()
        self.prompt_install(dialog)
        self.save_settings(dialog)

    def show_folder(self):
        if platform.system() == "Windows":
            os.startfile(str(self.prefix_path))
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(self.prefix_path)])
        else:
            subprocess.Popen(["xdg-open", str(self.prefix_path)])

    def _migrate_old_dependencies(self):
        """
        Detect and clean up the old flat dependencies layout.

        Before version-specific folders were introduced, packages were installed
        directly into python/dependencies/. After a Python version change (e.g.
        QGIS 3→4 profile migration), these packages may be incompatible.

        If the old layout is detected (dist-info directories directly in
        base_deps_path), remove it so dependencies get cleanly reinstalled
        into the new version-specific folder.
        """
        if not self.base_deps_path.is_dir():
            log("Nothing to migrate starting clean")
            return

        # Check for dist-info dirs directly in the old flat layout
        has_old_packages = any(
            p.is_dir() and p.name.endswith(".dist-info")
            for p in self.base_deps_path.iterdir()
        )
        if not has_old_packages:
            return

        log(
            f"Old flat dependencies layout detected in {self.base_deps_path}. "
            f"Cleaning up for Python {sys.version_info.major}.{sys.version_info.minor}."
        )

        # Remove everything except version-specific subdirectories
        failed = []
        for item in self.base_deps_path.iterdir():
            # Keep existing version-specific folders (e.g. "3.12")
            if item.is_dir() and item.name[:1].isdigit() and "." in item.name:
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except OSError as e:
                failed.append(item.name)
                log(f"Failed to remove {item}: {e}")

        if failed:
            warn(
                f"Could not remove {len(failed)} items from old dependencies folder. "
                f"Files may be locked by the current session. "
                f"Please restart QGIS and try again."
            )
        else:
            log("Old dependencies removed. They will be reinstalled on next check.")

    def _check_on_startup(self):
        return self.settings.value("check_on_startup", "no") == "yes"

    def _check_on_install(self):
        return self.settings.value("check_on_install", "yes") == "yes"

    def _is_qgis_loaded(self):
        return self.iface.mainWindow().isVisible()
