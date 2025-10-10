"""
cx_Freeze setup script for SpotifyLyrics2
This uses a different bundling approach than PyInstaller
"""
import sys
from cx_Freeze import setup, Executable

# Dependencies are automatically detected, but some might need fine tuning
build_exe_options = {
    "packages": [
        "requests",
        "bs4",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "diskcache",
        "pathvalidate",
        "azapi",
        "xmltodict",
    ],
    "excludes": [
        "unidecode",  # Only used in unused chord services
        "tkinter",    # Not needed
        "unittest",   # Not needed
        "test",       # Not needed
        "PyQt5.QtQml",  # Not needed - QML imports causing error
        "PyQt5.QtQuick",  # Not needed
    ],
    "include_files": [
        ("icon.ico", "icon.ico"),
    ],
    "optimize": 2,
}

# GUI applications on Windows require base="Win32GUI"
base = None
if sys.platform == "win32":
    base = "Win32GUI"

setup(
    name="SpotifyLyrics2",
    version="2.0",
    description="SpotifyLyrics - Optimized with 9 working sources",
    options={"build_exe": build_exe_options},
    executables=[
        Executable(
            "SpotifyLyrics.pyw",
            base=base,
            target_name="SpotifyLyrics2.exe",
            icon="icon.ico",
        )
    ],
)
