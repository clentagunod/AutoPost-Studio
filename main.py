"""Launch the AutoPost Studio desktop application."""

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
    from src.app import main

    main()
