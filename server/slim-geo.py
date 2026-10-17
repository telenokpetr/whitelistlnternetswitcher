#!/usr/bin/env python3
"""Cut v2ray geoip.dat / geosite.dat down to a few categories (entries are copied verbatim).
usage: slim-geo.py <in.dat> <out.dat> <code1,code2,...>"""
import sys


def read_varint(buf, pos):
    shift = result = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def write_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def entry_code(body):
    """country_code is field 1 (string) of every entry."""
    pos = 0
    while pos < len(body):
        key, pos = read_varint(body, pos)
        field, wire = key >> 3, key & 7
        if wire == 2:
            length, pos = read_varint(body, pos)
            if field == 1:
                return body[pos:pos + length].decode("utf-8", "replace").upper()
            pos += length
        elif wire == 0:
            _, pos = read_varint(body, pos)
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            return ""
    return ""


def main():
    src, dst, codes = sys.argv[1], sys.argv[2], {c.strip().upper() for c in sys.argv[3].split(",")}
    data = open(src, "rb").read()
    pos, out, kept = 0, bytearray(), []
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field, wire = key >> 3, key & 7
        if wire != 2:
            raise SystemExit("unexpected wire type at top level")
        length, pos = read_varint(data, pos)
        body = data[pos:pos + length]
        pos += length
        if field == 1 and entry_code(body) in codes:
            out += write_varint((1 << 3) | 2) + write_varint(len(body)) + body
            kept.append(entry_code(body))
    if not kept:
        raise SystemExit("none of the requested categories were found")
    open(dst, "wb").write(out)
    print(f"{dst}: {len(out)} bytes, kept: {', '.join(sorted(kept))}")


main()
