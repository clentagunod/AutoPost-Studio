"""Launch AutoPost Studio or its detached scheduler worker."""

import sys

if __name__ == "__main__":
    if "--scheduler-worker" in sys.argv:
        from src.scheduler_service import run_scheduler_service

        run_scheduler_service()
    elif len(sys.argv) > 1 and sys.argv[1] == "cli":
        from src.automation_cli import main

        raise SystemExit(main(sys.argv[2:]))
    else:
        from src.app import main

        main()
