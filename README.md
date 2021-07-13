# QPIP

**WARNING - THIS IS IN EARLY DEVELOPEMENT, NOT STABLE/USABLE YET**

**QPIP** is a QGIS plugin allowing to install Python dependencies for other plugins.

When loading other plugins, it will check if a `requirements.txt` file exists in its directory. If so, it will verify if the dependencies are met, and display a dialog offering to install missing requirements.

All requirements are installed in the user's profile (under `python/dependencies`), so that each user profile can have a different set of dependencies.

## Usage (end user)

Install `QPIP` through the QGIS plugin manager. Once installed, by default, dependencies will be checked on each startup and plugin activation, and a dialog will offer to install missing dependencies.

A `QPIP` entry will appear  in the plugin menu :
- **Check dependencies now** : Checks requirements for all enabled plugins, and offers to install/upgrade missing requirements
- **Check dependencies on startup** : Toggles whether plugins are checked on QGIS startup. If you notice slowdown on plugin loading, disable this, and manually check dependencies. Note that dependencies will still be checked for new plugin installations.
- **Show installed** : Shows all installed PIP dependencies (both system wide, and in your user profile using QPIP)
- **Show skips** : Shows all skipped dependencies (dependencies for which the install dialog is skipped)

## Usage (how to integrate in your own QGIS plugin)

Add `plugin_dependencies=qpip` to your plugin's `metadata.txt` to ensure your user will have QPIP installed upon installation of your plugin.

Add a `requirements.txt` file in your plugin directory (see [an example](https://pip.pypa.io/en/stable/cli/pip_install/#example-requirements-file)).

**Important** : make sure to keep your requirements as loose as possible, as to minimise the risk of requirements conflicts with other plugins. Also, avoid requiring libraries that may conflict with core QGIS dependencies such as GDAL, as it could lead to instabilities.

## Limitations

QPIP handles each plugin independently. If two plugins have incomptabile requirements, the last one being installed will offer to upgrade the requirements, breaking the first one. In such cases, you may get the requirements dialog open on every launch. In such cases, you should contact the plugin authors, and see if they can make their dependencies compatible. Alternatively, you can install each plugin in a different user profile.


## How it works internally

- QPIP is installed under the `000_qpip` directory, so that it (hopefully) loads first
- `USERPROFILE/python/dependencies/Lib/site-packages` is added to sys.path
- `USERPROFILE/python/dependencies/Scripts` is added to the PATH
- `qgis.utils.loadPlugin` is monkeypatched, injecting code that checks requirements in `requirements.txt` using `pkg_resources`
- if requirements are met, the plugin is loaded directly
- if requirements are not met, loading the plugin is deferred
- once QGIS is initialized, for each deferred plugin:
  - a dialog is shown offering to install/upgrade the missing requirements
  - selected requirements are installed with `--prefix USERPROFILE/python/dependencies`
  - the plugin is loaded (even if requirements were not selected, in which case user would likely get an import error)


## Contribute

Style is enforced by pre-commit :
```
pip install pre-commit
pre-commit install
```

Deployements to QGIS plugin repository are made automatically by Github workflows when tags matching `v*` are pushed.
