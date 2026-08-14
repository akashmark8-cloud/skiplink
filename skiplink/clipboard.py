"""Clipboard helper that works without third-party packages."""

import shutil
import subprocess
import sys


def copy_text(text):
    """Copy `text` to the system clipboard using platform-native tools.

    Returns True on success, False otherwise.
    """
    system = sys.platform
    try:
        if system.startswith("linux"):
            for tool in ("xclip", "xsel"):
                path = shutil.which(tool)
                if not path:
                    continue
                if tool == "xclip":
                    args = [path, "-selection", "clipboard"]
                else:
                    args = [path, "--clipboard", "--input"]
                subprocess.run(args, input=text, text=True, check=True)
                return True
        elif system == "darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            return True
        elif system in ("win32", "cygwin"):
            subprocess.run(["clip"], input=text, text=True, check=True)
            return True
    except Exception:
        pass
    return False
