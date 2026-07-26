#!/usr/bin/env python3
"""po2lmo - Convert GNU gettext .po files to LuCI .lmo format.

Usage: python3 po2lmo.py input.po output.lmo

Implements the LuCI LMO binary format:
- Payload: concatenated value strings, 4-byte aligned
- Index: sorted by sfh_hash(key), 4x uint32 BE per entry
- Trailing uint32 BE: payload length (index offset)
"""

import struct, sys

def sfh_hash(data):
    """Super Fast Hash - Paul Hsieh, public domain"""
    if isinstance(data, str):
        data = data.encode('utf-8')
    d = data
    length = len(d)
    if length <= 0:
        return 0

    h = length & 0xFFFFFFFF
    rem = length & 3
    n = length >> 2
    pos = 0

    for _ in range(n):
        h = (h + (d[pos] | (d[pos+1] << 8))) & 0xFFFFFFFF
        tmp = (((d[pos+2] | (d[pos+3] << 8)) << 11) ^ h) & 0xFFFFFFFF
        h = ((h << 16) ^ tmp) & 0xFFFFFFFF
        pos += 4
        h = (h + (h >> 11)) & 0xFFFFFFFF

    if rem == 3:
        h = (h + (d[pos] | (d[pos+1] << 8))) & 0xFFFFFFFF
        h = (h ^ (h << 16)) & 0xFFFFFFFF
        h = (h ^ (d[pos+2] << 18)) & 0xFFFFFFFF
        h = (h + (h >> 11)) & 0xFFFFFFFF
    elif rem == 2:
        h = (h + (d[pos] | (d[pos+1] << 8))) & 0xFFFFFFFF
        h = (h ^ (h << 11)) & 0xFFFFFFFF
        h = (h + (h >> 17)) & 0xFFFFFFFF
    elif rem == 1:
        h = (h + d[pos]) & 0xFFFFFFFF
        h = (h ^ (h << 10)) & 0xFFFFFFFF
        h = (h + (h >> 1)) & 0xFFFFFFFF

    h = (h ^ (h << 3)) & 0xFFFFFFFF
    h = (h + (h >> 5)) & 0xFFFFFFFF
    h = (h ^ (h << 4)) & 0xFFFFFFFF
    h = (h + (h >> 17)) & 0xFFFFFFFF
    h = (h ^ (h << 25)) & 0xFFFFFFFF
    h = (h + (h >> 6)) & 0xFFFFFFFF

    return h


def po2lmo(po_path, lmo_path):
    """Convert a .po file to .lmo binary format."""
    entries = []
    current_id = None
    current_str = None
    current_ctxt = None
    in_msgid = False
    in_msgstr = False

    with open(po_path, 'r', encoding='utf-8') as f:
        content = f.read()

    for line in content.split('\n'):
        s = line.strip()
        if s.startswith('#') or s == '':
            continue

        if s.startswith('msgctxt "'):
            if current_id is not None and current_str and current_id.strip():
                key = (current_ctxt + '\x01' + current_id) if current_ctxt else current_id
                entries.append((key, current_str))
            current_ctxt = s[9:-1]
            current_id = None
            current_str = None
            in_msgid = False
            in_msgstr = False
        elif s.startswith('msgid "'):
            if current_id is not None and current_str and current_id.strip():
                key = (current_ctxt + '\x01' + current_id) if current_ctxt else current_id
                entries.append((key, current_str))
            current_id = s[7:-1]
            current_str = None
            in_msgid = True
            in_msgstr = False
        elif s.startswith('msgstr "'):
            current_str = s[8:-1]
            in_msgid = False
            in_msgstr = True
        elif s.startswith('"') and in_msgid:
            current_id += s[1:-1]
        elif s.startswith('"') and in_msgstr:
            current_str += s[1:-1]

    if current_id is not None and current_str and current_id.strip():
        key = (current_ctxt + '\x01' + current_id) if current_ctxt else current_id
        entries.append((key, current_str))

    # Filter invalid entries
    entries = [(k, v) for k, v in entries if v and k.strip()]

    # Build payload and index
    payload = bytearray()
    index = []
    offset = 0

    for key, val in entries:
        val_bytes = val.encode('utf-8')
        val_len = len(val_bytes)
        padding = (4 - (val_len % 4)) % 4

        index.append((sfh_hash(key), 1, offset, val_len))
        payload.extend(val_bytes)
        payload.extend(b'\x00' * padding)
        offset += val_len + padding

    index.sort(key=lambda e: e[0])

    with open(lmo_path, 'wb') as f:
        f.write(bytes(payload))
        for e in index:
            f.write(struct.pack('>I', e[0]))  # key_id
            f.write(struct.pack('>I', e[1]))  # val_id
            f.write(struct.pack('>I', e[2]))  # offset
            f.write(struct.pack('>I', e[3]))  # length
        f.write(struct.pack('>I', len(payload)))  # index offset

    total = len(payload) + len(index) * 16 + 4
    print(f'{lmo_path}: {len(index)} entries, {total} bytes')
    return True


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f'Usage: {sys.argv[0]} input.po output.lmo')
        sys.exit(1)
    po2lmo(sys.argv[1], sys.argv[2])
