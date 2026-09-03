"""Console entry point collected beside the windowed application."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    from ccs_monitor.map_fusion_worker import main
    raise SystemExit(main())
