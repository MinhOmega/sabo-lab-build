#!/usr/bin/env python3
"""Trusted stdin-gated child: Windows parent assigns its Job Object first."""
import json
import subprocess
import sys

if __name__ == '__main__':
    request = json.load(sys.stdin)
    result = subprocess.run(request['args'], cwd=request['cwd'], stdin=subprocess.DEVNULL)
    sys.exit(result.returncode)
