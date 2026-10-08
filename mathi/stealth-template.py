#!/usr/bin/env python3
# Wrapper to allow running with the exact original filename 'stealth-template.py'
import stealth_template

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(stealth_template._cli_main())
    except KeyboardInterrupt:
        pass
