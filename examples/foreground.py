"""Read-only automation smoke test.

Run with:
    python -m phonectl run examples/foreground.py -s SERIAL
"""


def run(dev):
    dev.print("serial:", dev.serial)
    dev.print("screen:", dev.size)
    dev.print("foreground:", dev.current_app())
