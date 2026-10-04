"""Launch AutoPost Studio or its CLI."""

import sys

if __name__ == "__main__":
    if "--scheduler-worker" in sys.argv:
        if sys.stderr is not None:
            print(
                "The detached local scheduler has been removed. "
                "Schedule posts directly with Meta from the scheduler GUI.",
                file=sys.stderr,
            )
        raise SystemExit(2)
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from src.automation_cli import main

        raise SystemExit(main(sys.argv[2:]))
    else:
        from src.app import main

        main()
