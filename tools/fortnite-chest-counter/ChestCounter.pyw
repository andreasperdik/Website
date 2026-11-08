# Double-click this file to open Chest Counter without a console window.
# (Windows runs .pyw files with pythonw.exe.)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chest_counter_app import main  # noqa: E402

main()
