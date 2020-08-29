#!/usr/bin/env python3

import sys
from textwrap import dedent


def main(argv):
    print(dedent(f"""\
    test-{argv[0]}:
      - echo generated test variant
    """), end='')


if __name__ == "__main__":
    main(sys.argv[1:])
