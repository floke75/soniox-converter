"""Package entry point for ``python -m soniox_converter``.

WHY: Users run the converter as ``python -m soniox_converter input.mp4``
for CLI mode, or ``python -m soniox_converter --gui`` for the desktop GUI.
Python's ``-m`` flag looks for ``__main__.py`` inside the package and
executes it.

HOW: Checks sys.argv for the ``--gui`` flag. If present, launches the
Tkinter GUI. Otherwise, delegates to the CLI's main() function.

RULES:
- This file must exist for ``python -m soniox_converter`` to work
- ``--gui`` flag launches the Tkinter GUI
- Without ``--gui``, falls through to the CLI
- The ``if __name__`` guard is technically redundant here (Python
  always executes __main__.py as __main__), but included for clarity
"""

import sys

if __name__ == "__main__":
    if "--gui" in sys.argv:
        from soniox_converter.gui import main as gui_main
        gui_main()
    else:
        from soniox_converter.cli import main
        main()
