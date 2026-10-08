#!/usr/bin/env python3
# Wrapper to allow running with the exact original filename 'search-and-scrape.py'
import search_and_scrape

if __name__ == "__main__":
    import asyncio
    import sys
    try:
        asyncio.run(search_and_scrape.main())
    except Exception as err:
        print(err, file=sys.stderr)
        sys.exit(1)
