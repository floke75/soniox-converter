"""GUI launcher wrapper that ensures proper macOS foreground app status.

WHY: Tkinter with system Tcl/Tk 8.5 on macOS crashes (SIGABRT in TkpInit)
when launched from a .command file because the process doesn't have proper
GUI/foreground app status with the window server.

HOW: Uses PyObjC (bundled with macOS system Python) to call
NSApplication.sharedApplication() before importing tkinter. This registers
the process as a GUI application with the window server, preventing the
TkpInit crash.

RULES:
- Must be called BEFORE any tkinter import.
- Only applies to macOS; no-op on other platforms.
"""

import sys
import os


def _ensure_macos_gui_app():
    """Register this process as a macOS GUI application."""
    if sys.platform != "darwin":
        return
    try:
        import AppKit  # noqa: F401 — PyObjC, bundled with macOS system Python
        AppKit.NSApplication.sharedApplication()
    except ImportError:
        # PyObjC not available — try ctypes approach
        try:
            import ctypes
            import ctypes.util
            appkit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("AppKit"))
            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))

            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_msgSend.restype = ctypes.c_void_p
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

            NSApplication = objc.objc_getClass(b"NSApplication")
            sel = objc.sel_registerName(b"sharedApplication")
            objc.objc_msgSend(NSApplication, sel)
        except Exception:
            pass  # Last resort: hope tkinter works anyway


if __name__ == "__main__":
    _ensure_macos_gui_app()

    # Now safe to import and run the GUI
    from soniox_converter.gui import main
    main()
