"""
setup.py - optional py2app build script.

Only needed if you want a real double-clickable Sims4TrayChecker.app
with the custom icon baked in. See ../icon/README.md for the full
walkthrough. Not required to just run the app with `python3 app.py`.

Usage (on your Mac, after `pip3 install py2app`):
    python3 setup.py py2app
"""

from setuptools import setup

APP = ["app.py"]
ICON_PATH = "../icon/build/AppIcon.icns"  # build this first via iconutil, see ../icon/README.md

OPTIONS = {
    "argv_emulation": False,
    "iconfile": ICON_PATH,
    "plist": {
        "CFBundleName": "Sims4TrayChecker",
        "CFBundleDisplayName": "Sims 4 Tray & CC Health Checker",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
    },
    "packages": ["PIL"],
}

setup(
    app=APP,
    name="Sims4TrayChecker",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
