"""Desktop entry point with a noninteractive isolated smoke-test error report."""
import sys

try:
    from subtitleflow.gui import main
    if __name__ == "__main__":
        main()
except Exception:
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        import json
        from pathlib import Path
        import traceback
        Path(sys.argv[2]).write_text(json.dumps({"ok": False, "traceback": traceback.format_exc()}, indent=2), encoding="utf-8")
        sys.exit(1)
    raise
