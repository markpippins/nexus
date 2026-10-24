"""Allow ``python3 -m nebula`` as an alias for ``python3 -m nebula.cli``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
