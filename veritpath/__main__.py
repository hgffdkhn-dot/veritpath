import sys

try:  # normal `python -m veritpath`
    from veritpath.cli import main
except ImportError:  # executed as a plain script / frozen binary
    from cli import main

if __name__ == "__main__":
    sys.exit(main())
