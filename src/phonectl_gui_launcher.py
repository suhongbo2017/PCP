"""Double-click GUI entry point for the standalone Windows application."""

from phonectl.ui.app import run_gui


if __name__ == "__main__":
    raise SystemExit(run_gui(auto_connect=True))
