#!/usr/bin/env python3
"""Common utilities and parsers shared by bffnt packer/unpacker."""
from typing import Tuple, Dict, Any, List
import struct

try:
    from PIL import Image  # noqa: F401
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

SIG_TGLP = b"TGLP"
SIG_FINF = b"FINF"


def read_u16(data: bytes, off: int, le: bool) -> Tuple[int, int]:
    return (struct.unpack_from('<H' if le else '>H', data, off)[0], off + 2)


def read_u32(data: bytes, off: int, le: bool) -> Tuple[int, int]:
    return (struct.unpack_from('<I' if le else '>I', data, off)[0], off + 4)


def detect_endian_and_version(buf: bytes, sig: bytes) -> Tuple[bool, int, int]:
    if len(buf) < 10:
        raise ValueError('Файл надто малий або пошкоджений')
    bom_be = struct.unpack_from('>H', buf, 4)[0]
    little = (bom_be == 0xFFFE)
    if sig in (b'RFNT', b'TNFR', b'RFNA'):
        ver = struct.unpack_from('<H' if little else '>H', buf, 8)[0]
        hdr_size = struct.unpack_from('<H' if little else '>H', buf, 14)[0]
        return little, ver, hdr_size
    else:
        hdr_size = struct.unpack_from('<H' if little else '>H', buf, 6)[0]
        ver = struct.unpack_from('<I' if little else '>I', buf, 8)[0]
        return little, ver, hdr_size


def determine_platform(sig: bytes, little: bool, version: int) -> str:
    if sig in (b'RFNT', b'TNFR', b'RFNA'):
        return 'Wii'
    if sig == b'CFNT':
        return 'Ctr'
    if not little:
        return 'Cafe'
    return 'NX' if version >= 0x04010000 else 'Ctr'


def find_section(buf: bytes, fourcc: bytes) -> int:
    idx = buf.find(fourcc)
    if idx < 0:
        raise ValueError(f'Секцію {fourcc.decode()} не знайдено')
    return idx


def parse_finf(buf: bytes, base_off: int, little: bool, platform: str, version: int) -> Tuple[Dict[str, Any], Dict[str, int]]:
    if buf[base_off:base_off+4] != SIG_FINF:
        raise ValueError('FINF не на очікуваній позиції')
    off = base_off + 4
    _size, off = read_u32(buf, off, little)

    finf: Dict[str, Any] = {}
    tglp_ofs = cwdh_ofs = cmap_ofs = 0
    if (platform in ('Ctr',) and version < 0x04000000):
        finf['type'] = buf[off]; off += 1
        finf['line_feed'] = buf[off]; off += 1
        finf['alter_char_index'], off = read_u16(buf, off, little)
        finf['default_left'] = buf[off]; off += 1
        finf['default_glyph'] = buf[off]; off += 1
        finf['default_char'] = buf[off]; off += 1
        finf['char_encoding'] = buf[off]; off += 1
        tglp_ofs, off = read_u32(buf, off, little)
        cwdh_ofs, off = read_u32(buf, off, little)
        cmap_ofs, off = read_u32(buf, off, little)
        finf['height'] = buf[off]; off += 1
        finf['width'] = buf[off]; off += 1
        finf['ascent'] = buf[off]; off += 1
        off += 1
    else:
        finf['type'] = buf[off]; off += 1
        finf['height'] = buf[off]; off += 1
        finf['width'] = buf[off]; off += 1
        finf['ascent'] = buf[off]; off += 1
        finf['line_feed'], off = read_u16(buf, off, little)
        finf['alter_char_index'], off = read_u16(buf, off, little)
        finf['default_left'] = buf[off]; off += 1
        finf['default_glyph'] = buf[off]; off += 1
        finf['default_char'] = buf[off]; off += 1
        finf['char_encoding'] = buf[off]; off += 1
        tglp_ofs, off = read_u32(buf, off, little)
        cwdh_ofs, off = read_u32(buf, off, little)
        cmap_ofs, off = read_u32(buf, off, little)
    return finf, {'tglp': tglp_ofs, 'cwdh': cwdh_ofs, 'cmap': cmap_ofs}


def parse_tglp_and_extract(buf: bytes, tglp_off: int, little: bool, platform: str, signature: bytes) -> Tuple[Dict[str, Any], List[bytes]]:
    if buf[tglp_off:tglp_off+4] != SIG_TGLP:
        raise ValueError('TGLP не на очікуваній позиції')
    off = tglp_off + 4
    section_size, off = read_u32(buf, off, little)

    cell_width = buf[off]; off += 1
    cell_height = buf[off]; off += 1

    if platform in ('Ctr',) and signature != b'CFNT':
        pass

    sheet_count = buf[off]; off += 1
    max_char_width = buf[off]; off += 1
    sheet_size, off = read_u32(buf, off, little)
    base_line_pos, off = read_u16(buf, off, little)
    fmt, off = read_u16(buf, off, little)
    row_count, off = read_u16(buf, off, little)
    col_count, off = read_u16(buf, off, little)
    sheet_width, off = read_u16(buf, off, little)
    sheet_height, off = read_u16(buf, off, little)
    sheet_data_off, off = read_u32(buf, off, little)

    sheets: List[bytes] = []
    if sheet_data_off <= 0 or sheet_data_off >= len(buf):
        raise ValueError('Некоректний офсет даних аркушів у TGLP')
    pos = sheet_data_off
    for _ in range(sheet_count):
        end = pos + sheet_size
        if end > len(buf):
            raise ValueError('Аркуш виходить за межі файлу')
        sheets.append(buf[pos:end])
        pos = end

    tglp = {
        'cell_width': cell_width,
        'cell_height': cell_height,
        'max_char_width': max_char_width,
        'base_line': base_line_pos,
        'sheet_size': sheet_size,
        'sheet_count': sheet_count,
        'format': fmt,
        'rows': row_count,
        'cols': col_count,
        'sheet_width': sheet_width,
        'sheet_height': sheet_height,
        'sheet_data_off': sheet_data_off,
    }
    return tglp, sheets


def parse_cwdh_chain(buf: bytes, start_off: int, little: bool) -> Dict[int, Dict[str, int]]:
    widths_by_index: Dict[int, Dict[str, int]] = {}
    off = start_off
    visited = set()
    while off and off not in visited:
        visited.add(off)
        if buf[off:off+4] != b'CWDH':
            raise ValueError('Очікував CWDH на офсеті 0x%X' % off)
        p = off + 4
        section_size, p = read_u32(buf, p, little)
        start_idx, p = read_u16(buf, p, little)
        end_idx, p = read_u16(buf, p, little)
        next_ofs, p = read_u32(buf, p, little)
        count = end_idx - start_idx + 1
        for i in range(count):
            left = struct.unpack_from('b', buf, p)[0]; p += 1
            glyph_w = buf[p]; p += 1
            char_w = buf[p]; p += 1
            widths_by_index[start_idx + i] = {
                'left': int(left),
                'glyph': int(glyph_w),
                'char': int(char_w),
            }
        off = (next_ofs - 8) if next_ofs else 0
    return widths_by_index


def parse_cmap_chain(buf: bytes, start_off: int, little: bool, platform: str) -> Dict[int, int]:
    cmap: Dict[int, int] = {}
    off = start_off
    visited = set()
    while off and off not in visited:
        visited.add(off)
        if buf[off:off+4] != b'CMAP':
            raise ValueError('Очікував CMAP на офсеті 0x%X' % off)
        p = off + 4
        section_size, p = read_u32(buf, p, little)
        if platform == 'NX':
            code_begin, p = read_u32(buf, p, little)
            code_end, p = read_u32(buf, p, little)
        else:
            code_begin, p = read_u16(buf, p, little)
            code_end, p = read_u16(buf, p, little)
        mapping_method, p = read_u16(buf, p, little)
        p += 2
        next_ofs, p = read_u32(buf, p, little)

        # Important: when chaining CMAP segments, earlier segments (closer to head)
        # must take precedence. Do not overwrite existing entries from prior segments.
        if mapping_method == 0:
            char_offset, p = read_u16(buf, p, little)
            for cc in range(code_begin, code_end + 1):
                idx = cc - code_begin + char_offset
                if idx < 0xFFFF and cc not in cmap:
                    cmap[cc] = idx
        elif mapping_method == 1:
            for cc in range(code_begin, code_end + 1):
                idx = struct.unpack_from('<h' if little else '>h', buf, p)[0]
                p += 2
                if idx != -1 and cc not in cmap:
                    cmap[cc] = idx
        elif mapping_method == 2:
            count, p = read_u16(buf, p, little)
            if platform == 'NX':
                p += 2
                for _ in range(count):
                    cc, p = read_u32(buf, p, little)
                    idx = struct.unpack_from('<h' if little else '>h', buf, p)[0]; p += 2
                    p += 2
                    if idx != -1 and cc not in cmap:
                        cmap[cc] = idx
            else:
                for _ in range(count):
                    cc, p = read_u16(buf, p, little)
                    idx = struct.unpack_from('<h' if little else '>h', buf, p)[0]; p += 2
                    if idx != -1 and cc not in cmap:
                        cmap[cc] = idx
        else:
            raise ValueError('Невідомий метод CMAP: %d' % mapping_method)

        off = (next_ofs - 8) if next_ofs else 0
    return cmap


# ---------------- GX2 helpers + BC4 encode/decode (shared) ----------------

def _compute_pixel_index_microtile(x: int, y: int, bpp_bits: int) -> int:
    if bpp_bits == 8:
        pb0 = x & 1; pb1 = (x & 2) >> 1; pb2 = (x & 4) >> 2
        pb3 = (y & 2) >> 1; pb4 = y & 1; pb5 = (y & 4) >> 2
    elif bpp_bits == 0x10:
        pb0 = x & 1; pb1 = (x & 2) >> 1; pb2 = (x & 4) >> 2
        pb3 = y & 1; pb4 = (y & 2) >> 1; pb5 = (y & 4) >> 2
    elif bpp_bits in (0x20, 0x60):
        pb0 = x & 1; pb1 = (x & 2) >> 1; pb2 = y & 1
        pb3 = (x & 4) >> 2; pb4 = (y & 2) >> 1; pb5 = (y & 4) >> 2
    elif bpp_bits == 0x40:
        pb0 = x & 1; pb1 = y & 1; pb2 = (x & 2) >> 1
        pb3 = (x & 4) >> 2; pb4 = (y & 2) >> 1; pb5 = (y & 4) >> 2
    elif bpp_bits == 0x80:
        pb0 = y & 1; pb1 = x & 1; pb2 = (x & 2) >> 1
        pb3 = (x & 4) >> 2; pb4 = (y & 2) >> 1; pb5 = (y & 4) >> 2
    else:
        pb0 = x & 1; pb1 = (x & 2) >> 1; pb2 = y & 1
        pb3 = (x & 4) >> 2; pb4 = (y & 2) >> 1; pb5 = (y & 4) >> 2
    return (32 * pb5) | (16 * pb4) | (8 * pb3) | (4 * pb2) | pb0 | (2 * pb1)


def _pipe_from_xy(x: int, y: int) -> int:
    return ((y >> 3) ^ (x >> 3)) & 1


def _bank_from_xy(x: int, y: int) -> int:
    return (((y >> 5) ^ (x >> 3)) & 1) | (2 * (((y >> 4) ^ (x >> 4)) & 1))


def _addr_from_coord_macrotiled_bc4(x: int, y: int, pitch: int, height: int,
                                    pipe_swizzle: int = 0, bank_swizzle: int = 0) -> int:
    micro_tile_thickness = 1
    num_samples = 1
    bpp_bits = 64
    micro_tile_bits = num_samples * bpp_bits * (micro_tile_thickness * 64)
    micro_tile_bytes = (micro_tile_bits + 7) // 8

    pixel_index = _compute_pixel_index_microtile(x & 7, y & 7, bpp_bits)
    bytes_per_sample = micro_tile_bytes // num_samples
    sample_offset = 0
    pixel_offset_bits = bpp_bits * pixel_index
    elem_offset_bits = pixel_offset_bits + sample_offset
    elem_offset = (elem_offset_bits + 7) // 8

    pipe = _pipe_from_xy(x, y)
    bank = _bank_from_xy(x, y)

    swizzle = (pipe_swizzle + 2 * bank_swizzle) & 0xFFFFFFFF
    bank_pipe = (pipe + 2 * bank) ^ (swizzle % 8)
    bank_pipe %= 8
    pipe = bank_pipe % 2
    bank = bank_pipe // 2

    slice_bytes = (height * pitch * micro_tile_thickness * bpp_bits * num_samples + 7) // 8
    slice_offset = 0

    macro_tile_pitch = 32
    macro_tile_height = 16

    macro_tiles_per_row = pitch // macro_tile_pitch
    macro_tile_bytes = (num_samples * micro_tile_thickness * bpp_bits * macro_tile_height * macro_tile_pitch + 7) // 8
    macro_tile_index_x = x // macro_tile_pitch
    macro_tile_index_y = y // macro_tile_height

    def compute_bank_swapped_width(pitch_blocks: int) -> int:
        bpp = 8
        bytesPerSample = 8 * bpp
        bytesPerTileSlice = 1 * bytesPerSample // 1
        factor = 1
        swapTiles = max(1, 128 // bpp)
        swapWidth = swapTiles * 32
        heightBytes = 1 * factor * bpp * 2 // 1
        swapMax = 0x4000 // heightBytes
        swapMin = 256 // bytesPerTileSlice
        bankSwapWidth = min(swapMax, max(swapMin, swapWidth))
        while bankSwapWidth >= 2 * pitch_blocks:
            bankSwapWidth >>= 1
        return bankSwapWidth

    bank_swap_order = [0, 1, 3, 2, 6, 7, 5, 4, 0, 0]
    bank_swapped_width = compute_bank_swapped_width(pitch)
    if bank_swapped_width:
        swap_index = (macro_tile_pitch * macro_tile_index_x) // bank_swapped_width
        bank ^= bank_swap_order[swap_index & 3]

    macro_tile_offset = (macro_tile_index_x + macro_tiles_per_row * macro_tile_index_y) * macro_tile_bytes
    total_offset = elem_offset + ((macro_tile_offset + slice_offset) >> 3)
    return (bank << 9) | (pipe << 8) | (total_offset & 255) | (((total_offset & ~255) << 3) & 0xFFFFFFFF)


def _deswizzle_bc4_gx2_blocks(swizzled: bytes, width_blocks: int, height_blocks: int,
                              sheet_index: int) -> bytes:
    out = bytearray(len(swizzled))
    pitch = width_blocks
    pipe_sw = 0
    bank_sw = sheet_index & 3
    for y in range(height_blocks):
        for x in range(width_blocks):
            src = _addr_from_coord_macrotiled_bc4(x, y, pitch, height_blocks, pipe_sw, bank_sw)
            dst = (y * width_blocks + x) * 8
            out[dst:dst+8] = swizzled[src:src+8]
    return bytes(out)


def _decode_bc4_block(block: bytes) -> List[int]:
    a0 = block[0]
    a1 = block[1]
    bits = int.from_bytes(block[2:8], 'little')
    palette = [0] * 8
    palette[0] = a0
    palette[1] = a1
    if a0 > a1:
        for i in range(1, 7):
            palette[1 + i] = ((6 - i) * a0 + i * a1 + 3) // 7
    else:
        for i in range(1, 5):
            palette[1 + i] = ((4 - i) * a0 + i * a1 + 2) // 5
        palette[6] = 0
        palette[7] = 255
    vals = [0] * 16
    for i in range(16):
        idx = (bits >> (3 * i)) & 0x7
        vals[i] = palette[idx]
    return vals


def _swizzle_linear_bc4_to_gx2_blocks(linear_blocks: bytes, width_blocks: int, height_blocks: int, sheet_index: int) -> bytes:
    out = bytearray(len(linear_blocks))
    pitch = width_blocks
    pipe_sw = 0
    bank_sw = sheet_index & 3
    for y in range(height_blocks):
        for x in range(width_blocks):
            dst = _addr_from_coord_macrotiled_bc4(x, y, pitch, height_blocks, pipe_sw, bank_sw)
            src = (y * width_blocks + x) * 8
            out[dst:dst+8] = linear_blocks[src:src+8]
    return bytes(out)


def _encode_bc4_block(pvals: List[int]) -> bytes:
    if not pvals:
        return b"\x00\x00" + b"\x00" * 6
    mn = min(pvals)
    mx = max(pvals)
    a0 = int(mx)
    a1 = int(mn)
    if a0 == a1:
        bits = 0
        return bytes([a0 & 0xFF, a1 & 0xFF]) + int(bits).to_bytes(6, 'little')
    palette = [0] * 8
    palette[0] = a0
    palette[1] = a1
    for i in range(1, 7):
        palette[1 + i] = ((6 - i) * a0 + i * a1 + 3) // 7
    idxs = []
    for v in pvals:
        best_i = 0
        best_d = 1e9
        for i, pv in enumerate(palette):
            d = abs(int(v) - int(pv))
            if d < best_d:
                best_d = d; best_i = i
        idxs.append(best_i & 7)
    bits = 0
    for i, iv in enumerate(idxs):
        bits |= (iv & 7) << (3 * i)
    return bytes([a0 & 0xFF, a1 & 0xFF]) + int(bits).to_bytes(6, 'little')


def _encode_png_to_bc4_gx2(img, sheet_w: int, sheet_h: int, sheet_index: int) -> bytes:
    from PIL import Image as _Img  # type: ignore
    if img.size != (sheet_w, sheet_h):
        raise ValueError('Розмір PNG не збігається з очікуваним %dx%d' % (sheet_w, sheet_h))
    comp = img.getchannel('A') if img.mode == 'RGBA' else img.convert('L')
    bw = sheet_w // 4
    bh = sheet_h // 4
    if bw * 4 != sheet_w or bh * 4 != sheet_h:
        raise ValueError('Розмір аркуша не кратний 4 для BC4')
    lin = bytearray(bw * bh * 8)
    pix = comp.load()
    off = 0
    for by in range(bh):
        for bx in range(bw):
            vals = []
            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    vals.append(int(pix[x, y]))
            blk = _encode_bc4_block(vals)
            lin[off:off+8] = blk
            off += 8
    return _swizzle_linear_bc4_to_gx2_blocks(bytes(lin), bw, bh, sheet_index)
