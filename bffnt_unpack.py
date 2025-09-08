#!/usr/bin/env python3
"""Unpack routines for BFFNT/BCFNT/BRFNT."""
import os
import json
import struct
import shutil
from typing import Dict, Any, List

from bffnt_common import (
    _HAS_PIL,
    SIG_TGLP,
    SIG_FINF,
    detect_endian_and_version,
    determine_platform,
    find_section,
    parse_finf,
    parse_tglp_and_extract,
    parse_cwdh_chain,
    parse_cmap_chain,
    _deswizzle_bc4_gx2_blocks,
    _decode_bc4_block,
)

try:
    from PIL import Image  # noqa: F401
except Exception:
    Image = None


def _decode_sheet_pixels_bc4_gx2(data: bytes, width: int, height: int, sheet_index: int):
    if not _HAS_PIL:
        raise RuntimeError('PIL потрібен для перевірки')
    bw = width // 4
    bh = height // 4
    expected_size = bw * bh * 8
    if expected_size != len(data):
        raise ValueError('Неспівпадіння розміру для BC4: потрібні %d байт' % expected_size)
    lin_blocks = _deswizzle_bc4_gx2_blocks(data, bw, bh, sheet_index)
    img = Image.new('L', (width, height))
    pix = img.load()
    off = 0
    for by in range(bh):
        for bx in range(bw):
            block = lin_blocks[off:off+8]
            off += 8
            vals = _decode_bc4_block(block)
            for py in range(4):
                for px_i in range(4):
                    v = vals[py * 4 + px_i]
                    x = bx * 4 + px_i
                    y = by * 4 + py
                    pix[x, y] = v
    return img


def decode_sheet_to_png_bc4_gx2(data: bytes, width: int, height: int, out_path: str, sheet_index: int, rotate180: bool = False, flip_y: bool = False) -> None:
    bw = width // 4
    bh = height // 4
    expected_size = bw * bh * 8
    if expected_size != len(data):
        raise ValueError('Неспівпадіння розміру для BC4: потрібні %d байт' % expected_size)
    lin_blocks = _deswizzle_bc4_gx2_blocks(data, bw, bh, sheet_index)
    if _HAS_PIL:
        img = Image.new('RGBA', (width, height))
        pix = img.load()
    else:
        buf = bytearray(width * height)
    off = 0
    for by in range(bh):
        for bx in range(bw):
            block = lin_blocks[off:off+8]
            off += 8
            vals = _decode_bc4_block(block)
            for py in range(4):
                for px_i in range(4):
                    v = vals[py * 4 + px_i]
                    x = bx * 4 + px_i
                    y = by * 4 + py
                    if _HAS_PIL:
                        pix[x, y] = (255, 255, 255, v)
                    else:
                        buf[y * width + x] = v
    if _HAS_PIL:
        if flip_y:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        if rotate180:
            img = img.rotate(180)
        img.save(out_path, format='PNG')
    else:
        with open(out_path.replace('.png', '.pgm'), 'wb') as wf:
            header = f"P5\n{width} {height}\n255\n".encode('ascii')
            wf.write(header)
            wf.write(buf)


def unpack_bffnt(path: str, rotate180: bool = False, flip_y: bool = False, verbose: bool = False) -> str:
    with open(path, 'rb') as f:
        buf = f.read()

    if len(buf) < 16:
        raise ValueError('Файл пошкоджений або порожній')

    sig = buf[0:4]
    if sig not in (b'FFNT', b'CFNT', b'RFNT', b'TNFR', b'RFNA'):
        raise ValueError('Невідома сигнатура: %r' % sig)

    little, version, header_size = detect_endian_and_version(buf, sig)
    platform = determine_platform(sig, little, version)

    finf_off = find_section(buf, SIG_FINF)
    finf, offs = parse_finf(buf, finf_off, little, platform, version)

    tglp_off = (offs['tglp'] - 8) if offs['tglp'] else find_section(buf, SIG_TGLP)
    cwdh_off = (offs['cwdh'] - 8) if offs['cwdh'] else find_section(buf, b'CWDH')
    cmap_off = (offs['cmap'] - 8) if offs['cmap'] else find_section(buf, b'CMAP')

    tglp, sheets = parse_tglp_and_extract(buf, tglp_off, little, platform, sig)
    widths_by_index = parse_cwdh_chain(buf, cwdh_off, little)
    code_to_index = parse_cmap_chain(buf, cmap_off, little, platform)

    # Logging similar to pack: brief by default, detailed with BFFNT_VERBOSE=1
    verbose = bool(verbose) or bool(os.environ.get('BFFNT_VERBOSE'))
    try:
        print('[UNPACK] Формат:', platform, 'Endian:', 'LE' if little else 'BE')
        print('[UNPACK] FINF @ 0x%X; TGLP @ 0x%X; CWDH @ 0x%X; CMAP @ 0x%X' % (finf_off, tglp_off, cwdh_off, cmap_off))
        print('[UNPACK] Width entries:', len(widths_by_index), 'CMAP pairs:', len(code_to_index))
    except Exception:
        pass

    root = os.path.dirname(os.path.abspath(path))
    base = os.path.splitext(os.path.basename(path))[0]
    out_dir = os.path.join(root, base)
    # Clean existing output directory to avoid stale files from previous runs
    try:
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
    except Exception:
        pass
    os.makedirs(out_dir, exist_ok=True)

    meta: Dict[str, Any] = {
        'signature': sig.decode('ascii'),
        'bom': struct.unpack_from('>H', buf, 4)[0],
        'version': version,
        'header_size': header_size,
        'platform': platform,
        'finf': finf,
        'tglp': tglp,
    }

    row_count = int(tglp['rows'])
    col_count = int(tglp['cols'])
    per_sheet = row_count * col_count

    # Build glyph list sorted by glyph index to match sheet/grid order,
    # so neighbors in index (e.g., 146,147,148,149) appear together.
    glyphs: List[Dict[str, Any]] = []
    for cc, idx in sorted(code_to_index.items(), key=lambda kv: kv[1]):
        if int(cc) == 0xFFFF:
            # Пропускаємо службовий U+FFFF
            continue
        sheet = idx // per_sheet
        rem = idx % per_sheet
        grid_x = rem % row_count
        grid_y = rem // row_count
        w = widths_by_index.get(idx)
        if verbose:
            try:
                ch_disp = chr(cc) if 0 <= cc <= 0x10FFFF else ''
            except Exception:
                ch_disp = ''
            if w:
                print(f"[UNPACK] GLYPH: idx {idx} '{ch_disp}' U+{cc:04X} -> left={w.get('left')} glyph={w.get('glyph')} char={w.get('char')}")
            else:
                print(f"[UNPACK] GLYPH: idx {idx} '{ch_disp}' U+{cc:04X}")
        glyphs.append({
            'codepoint': f'U+{cc:04X}',
            'char': chr(cc) if 32 <= cc <= 0x10FFFF else '',
            'index': int(idx),
            'sheet': int(sheet),
            'grid_x': int(grid_x),
            'grid_y': int(grid_y),
            'width': w or None,
        })
    meta['glyphs'] = glyphs

    # Save sheets
    names = []
    for i, sh in enumerate(sheets):
        out_name = f'sheet_{i}.png'
        if rotate180:
            out_name = out_name.replace('.png', '.rot180.png')
        if flip_y:
            out_name = out_name.replace('.png', '.flipY.png')
        names.append(out_name)
        decode_sheet_to_png_bc4_gx2(sh, int(tglp['sheet_width']), int(tglp['sheet_height']), os.path.join(out_dir, out_name), i, rotate180=rotate180, flip_y=flip_y)
    if names:
        meta['sheet_png'] = names
    meta['png_ops'] = {'rotate180': bool(rotate180), 'flipY': bool(flip_y)}

    with open(os.path.join(out_dir, 'font.json'), 'w', encoding='utf-8') as jf:
        json.dump(meta, jf, ensure_ascii=False, indent=2)
    return out_dir
