#!/usr/bin/env python3
"""TOTP code for a site whose 2-step secret we hold, so no lane hands Jonathan a sign-in.

  otp_code.py <site>            prints the current 6-digit code (RFC 6238, SHA-1, 30 s) and nothing else
  otp_code.py --setup <site>    reads a setup key (base32) or an otpauth:// URI from stdin and stores it

The secret lives in ~/.config/afa/otp/<site> (override the folder with AFA_OTP_DIR), mode 600, in a
folder of mode 700. It is never printed, logged or echoed: errors name the site and the problem only.
"""
import base64
import binascii
import hashlib
import hmac
import os
import re
import struct
import sys
import time
from urllib.parse import parse_qs, urlparse

SITE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def otp_dir():
    return os.environ.get("AFA_OTP_DIR") or os.path.expanduser("~/.config/afa/otp")


def fail(msg, code=2):
    print("otp_code: " + msg, file=sys.stderr)
    sys.exit(code)


def parse_secret(text):
    """Accept a bare base32 key (spaces and case ignored) or an otpauth:// URI; return clean base32."""
    text = text.strip()
    if text.lower().startswith("otpauth://"):
        qs = parse_qs(urlparse(text).query)
        if "secret" not in qs:
            raise ValueError("no secret in the otpauth URI")
        text = qs["secret"][0]
    key = re.sub(r"[\s-]", "", text).upper().rstrip("=")
    if not key:
        raise ValueError("empty secret")
    try:
        base64.b32decode(key + "=" * (-len(key) % 8))
    except (binascii.Error, ValueError):
        raise ValueError("secret is not base32")
    return key


def totp(secret_b32, now=None, digits=6, step=30):
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    counter = int((time.time() if now is None else now) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    num = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(num).zfill(digits)


def site_path(site):
    if not SITE_RE.match(site):
        fail("site name must be lowercase letters, digits, dot, dash or underscore")
    return os.path.join(otp_dir(), site)


def setup(site):
    path = site_path(site)
    try:
        secret = parse_secret(sys.stdin.read())
    except ValueError as e:
        fail("setup refused: " + str(e))
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (secret + "\n").encode())
    finally:
        os.close(fd)


def code(site):
    path = site_path(site)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        fail("no secret stored for " + site + " (run --setup " + site + ")")
    if st.st_mode & 0o077:
        fail("%s has mode %o; it must be 600" % (path, st.st_mode & 0o777))
    with open(path) as fh:
        try:
            secret = parse_secret(fh.read())
        except ValueError as e:
            fail("stored secret for " + site + " is unusable: " + str(e))
    print(totp(secret))


def main(argv):
    if len(argv) == 3 and argv[1] == "--setup":
        setup(argv[2])
    elif len(argv) == 2 and not argv[1].startswith("-"):
        code(argv[1])
    else:
        fail("usage: otp_code.py <site> | otp_code.py --setup <site> (key on stdin)", 1)


if __name__ == "__main__":
    main(sys.argv)
