from __future__ import annotations

import os
import subprocess
import sys


PICKER_SCRIPT = """
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
try:
    selected = filedialog.askdirectory(title="Select workspace folder", mustexist=True)
finally:
    root.destroy()
print(selected or "", end="")
"""


def pick_workspace() -> str | None:
    if os.name != "nt":
        raise RuntimeError("Native workspace picking is only available on Windows.")

    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(
        [sys.executable, "-c", PICKER_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Native workspace picker failed.")
    return result.stdout.strip() or None
