#!/usr/bin/env python
from schwab.scripts.orders_codegen2 import latest_order_main

if __name__ == '__main__':
    import sys
    sys.exit(latest_order_main(sys.argv[1:]))
