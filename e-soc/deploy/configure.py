"""Create deployment secrets locally without printing their values."""
import os
from pathlib import Path
import re
import secrets
import sys


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r'(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}', sys.argv[1]):
        raise SystemExit('Usage: python3 deploy/configure.py soc.example.com')
    target = Path(__file__).resolve().parent.parent / '.env'
    domain = sys.argv[1].lower()
    content = (f'DOMAIN={domain}\nSECRET_KEY={secrets.token_hex(32)}\n'
               'SCAN_ALLOWLIST=127.0.0.0/8,::1/128\n')
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit('.env already exists; kept unchanged. Edit it on the server if needed.')
    with os.fdopen(fd, 'w') as output:
        output.write(content)
    print('Created .env with mode 0600. Secrets were not printed. Keep this file private.')

if __name__ == '__main__':
    main()
