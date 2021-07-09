# qpip

**WARNING - THIS IS IN EARLY DEVELOPEMENT, NOT STABLE/USABLE YET**

**qpip** is a helper for QGIS plugins to manage pip dependencies

## How it works

1. try to load before other plugins by using alphabetical prefix
  - not sure qgis.utils.updateAvailablePlugins() is perfectly deterministic ?
2. monkey patch `qgis.utils.loadPlugin` to inject code that :
   1. checks if pip_requirements is defined
   2. if so, check if requirements are met
   3. if not, defers loading of the plugin until this plugin's initGui was run
   4. shows a GUI to install missing deps
   5. once dialog was accepted, runs initial loadPlugin
