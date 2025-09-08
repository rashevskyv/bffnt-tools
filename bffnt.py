#!/usr/bin/env python3
"""
Призначення: розпаковка .bffnt/.bcfnt/.brfnt файлів у окрему теку поруч із файлом.

Результат:
- <name>/font.json — метадані (signature, platform, FINF, TGLP, glyphs)
- <name>/sheet_<i>[.rot180].png — аркуші гліфів (RGBA, прозорий фон)

Примітка щодо зображень: формати Cafe/Wii (GX2) зберігають аркуші у «тайлованому»
GPU-форматі (BC1–BC5 тощо). Для коректного PNG потрібна десвізл-розкладка та декодування
блоків. Це поза межами цього скрипта. Скріпт витягує сирі аркуші для подальшої обробки.

Використання:
  Розпакування:
    python3 bffnt.py [--rotate180] [--flipY] <шлях_до_*.bffnt>
    (або без аргументів — візьме CKingMain.bffnt у поточній теці)
    --rotate180  Перевернути вихідні PNG на 180° і додати суфікс .rot180 у назву.
    --flipY      Віддзеркалити вихідні PNG по вертикалі і додати суфікс .flipY у назву.
  Пакування (байт‑у‑біт як було):
    python3 bffnt.py pack <тека_з_font.json> [вихід.bffnt]
"""

import os
import sys
import json
import struct
import base64
from typing import Tuple, Dict, Any, List

try:
    from PIL import Image
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
    # Після сигнатури (4 байти) йде BOM (U16) з урахуванням big-endian читання
    if len(buf) < 10:
        raise ValueError('Файл надто малий або пошкоджений')
    bom_be = struct.unpack_from('>H', buf, 4)[0]
    little = (bom_be == 0xFFFE)

    # Для FFNT/CFNT: далі HeaderSize (u16) + Version (u32)
    # Для RFNT/TNFR/RFNA заголовок інший, але версію тут не критично мати
    if sig in (b'RFNT', b'TNFR', b'RFNA'):
        # Версія у цих варіантах 16-біт, але її мало де використовуємо
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
    # FFNT: NX при LE + версія >= 0x04010000, інакше Ctr; BE => Cafe
    if not little:
        return 'Cafe'
    return 'NX' if version >= 0x04010000 else 'Ctr'


def find_section(buf: bytes, fourcc: bytes) -> int:
    idx = buf.find(fourcc)
    if idx < 0:
        raise ValueError(f'Секцію {fourcc.decode()} не знайдено')
    return idx


def parse_finf(buf: bytes, base_off: int, little: bool, platform: str, version: int) -> Tuple[Dict[str, Any], Dict[str, int]]:
    # Очікуємо FINF на base_off
    if buf[base_off:base_off+4] != SIG_FINF:
        raise ValueError('FINF не на очікуваній позиції')
    off = base_off + 4
    _size, off = read_u32(buf, off, little)

    finf: Dict[str, Any] = {}
    # Дві гілки структури, як у Switch-Toolbox (див. FINF.cs)
    tglp_ofs = cwdh_ofs = cmap_ofs = 0
    if (platform in ('Ctr',) and version < 0x04000000):
        # Старий формат (спрощено: зберігаємо лише базові поля)
        finf['type'] = buf[off]; off += 1
        finf['line_feed'] = buf[off]; off += 1
        finf['alter_char_index'], off = read_u16(buf, off, little)
        finf['default_left'] = buf[off]; off += 1
        finf['default_glyph'] = buf[off]; off += 1
        finf['default_char'] = buf[off]; off += 1
        finf['char_encoding'] = buf[off]; off += 1
        # Офсети секцій
        tglp_ofs, off = read_u32(buf, off, little)
        cwdh_ofs, off = read_u32(buf, off, little)
        cmap_ofs, off = read_u32(buf, off, little)
        # Габарити
        finf['height'] = buf[off]; off += 1
        finf['width'] = buf[off]; off += 1
        finf['ascent'] = buf[off]; off += 1
        off += 1  # padding
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
        # Далі офсети секцій
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

    # Розгалуження за платформою/версією див. TGLP.cs
    if platform in ('Ctr',) and signature != b'CFNT':
        # Спрощено: підтримка головним чином Cafe/NX, тому Ctr тут не деталізуємо
        # Падаємо назад на Cafe/NX схему, щоб не зламати читання
        pass

    # Cafe/NX (основний шлях)
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
    }
    return tglp, sheets


# ---------------- GX2 (Wii U) десвізл для BC4 (спрощений) ----------------

def _compute_pixel_index_microtile(x: int, y: int, bpp_bits: int) -> int:
    # Відповідно до GX2.computePixelIndexWithinMicroTile
    if bpp_bits == 8:
        pb0 = x & 1; pb1 = (x & 2) >> 1; pb2 = (x & 4) >> 2
        pb3 = (y & 2) >> 1; pb4 = y & 1; pb5 = (y & 4) >> 2
    elif bpp_bits == 0x10:
        pb0 = x & 1; pb1 = (x & 2) >> 1; pb2 = (x & 4) >> 2
        pb3 = y & 1; pb4 = (y & 2) >> 1; pb5 = (y & 4) >> 2
    elif bpp_bits in (0x20, 0x60):
        pb0 = x & 1; pb1 = (x & 2) >> 1; pb2 = y & 1
        pb3 = (x & 4) >> 2; pb4 = (y & 2) >> 1; pb5 = (y & 4) >> 2
    elif bpp_bits == 0x40:  # 64 біт (BC4 у блоковому просторі)
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
    # Константи для ADDR_TM_2D_TILED_THIN1
    micro_tile_thickness = 1
    num_samples = 1
    bpp_bits = 64  # 64 біти на блок для BC4
    micro_tile_bits = num_samples * bpp_bits * (micro_tile_thickness * 64)
    micro_tile_bytes = (micro_tile_bits + 7) // 8  # 64 байт

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
    slice_offset = 0  # slice=0

    macro_tile_pitch = 32
    macro_tile_height = 16

    macro_tiles_per_row = pitch // macro_tile_pitch
    macro_tile_bytes = (num_samples * micro_tile_thickness * bpp_bits * macro_tile_height * macro_tile_pitch + 7) // 8
    macro_tile_index_x = x // macro_tile_pitch
    macro_tile_index_y = y // macro_tile_height
    # bank-swap корекція (спрощено, як у GX2.cs)
    def compute_bank_swapped_width(pitch_blocks: int) -> int:
        # bpp тут — байт на блок (BC4: 8)
        bpp = 8
        bytesPerSample = 8 * bpp
        # samplesPerTile = 2048 / bytesPerSample
        # slicesPerTile = 1 (бо numSamples=1)
        bytesPerTileSlice = 1 * bytesPerSample // 1
        factor = 1  # для THIN1
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
    # Фінальне байтове зміщення
    return (bank << 9) | (pipe << 8) | (total_offset & 255) | (((total_offset & ~255) << 3) & 0xFFFFFFFF)


def _deswizzle_bc4_gx2_blocks(swizzled: bytes, width_blocks: int, height_blocks: int,
                              sheet_index: int) -> bytes:
    out = bytearray(len(swizzled))
    # pitch у блоках: для більшості текстур — ширина у блоках (вирівняна); для 1024x1024 уже кратно 32
    pitch = width_blocks
    # Витягуємо swizzle біти так, як у Switch-Toolbox (див. Gx2ImageBlock.Swizzle)
    # pipe = ((sheet*2) >> 8) & 1 => завжди 0; bank = (sheet & 3)
    pipe_sw = 0
    bank_sw = sheet_index & 3
    for y in range(height_blocks):
        for x in range(width_blocks):
            src = _addr_from_coord_macrotiled_bc4(x, y, pitch, height_blocks, pipe_sw, bank_sw)
            dst = (y * width_blocks + x) * 8
            out[dst:dst+8] = swizzled[src:src+8]
    return bytes(out)


def _decode_bc4_block(block: bytes) -> List[int]:
    """Розпаковує один 4x4 блок BC4_UNORM до 16 значень 0..255 (рядок за рядком)."""
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


def _decode_sheet_pixels_bc4_gx2(data: bytes, width: int, height: int, sheet_index: int):
    """Повертає PIL Image у режимі 'L' (0..255) після десвізлу GX2 та декодування BC4."""
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
    """Декодує BC4_UNORM з десвізлом GX2 (2D_TILED_THIN1) у PNG (grayscale)."""
    bw = width // 4
    bh = height // 4
    expected_size = bw * bh * 8
    if expected_size != len(data):
        raise ValueError('Неспівпадіння розміру для BC4: потрібні %d байт' % expected_size)
    # Десвізл у блоковому просторі
    lin_blocks = _deswizzle_bc4_gx2_blocks(data, bw, bh, sheet_index)
    # Розпакування BC4 у пікселі
    if _HAS_PIL:
        # Робимо прозорий фон: RGB=білий, A=яскравість BC4
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

# ---------------- BC4 encode (naive) + GX2 swizzle ----------------

def _encode_bc4_block(pvals: list[int]) -> bytes:
    """Кодує один 4x4 блок (16 значень 0..255) у BC4_UNORM (8 байт).
    Наївний енкодер: бере min/max як кінцеві точки і квантує до найближчої палітри.
    """
    if not pvals:
        return b"\x00\x00" + b"\x00" * 6
    mn = min(pvals)
    mx = max(pvals)
    a0 = int(mx)
    a1 = int(mn)
    if a0 == a1:
        # плоский блок
        bits = 0
        return bytes([a0 & 0xFF, a1 & 0xFF]) + int(bits).to_bytes(6, 'little')
    # Палітра для варіанту a0 > a1 (8 значень)
    palette = [0] * 8
    palette[0] = a0
    palette[1] = a1
    for i in range(1, 7):
        palette[1 + i] = ((6 - i) * a0 + i * a1 + 3) // 7
    # Присвоюємо індекси найближчих значень
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


def _encode_png_to_bc4_gx2(img, sheet_w: int, sheet_h: int, sheet_index: int) -> bytes:
    if img.size != (sheet_w, sheet_h):
        raise ValueError('Розмір PNG не збігається з очікуваним %dx%d' % (sheet_w, sheet_h))
    # Вибираємо канал: якщо RGBA — альфа, інакше L
    if img.mode == 'RGBA':
        comp = img.getchannel('A')
    else:
        comp = img.convert('L')
    # Кодуємо по блоках 4x4 у лінійний масив
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
    # Свізл до GX2 макротайлу
    return _swizzle_linear_bc4_to_gx2_blocks(bytes(lin), bw, bh, sheet_index)


def parse_cwdh_chain(buf: bytes, start_off: int, little: bool) -> Dict[int, Dict[str, int]]:
    """Читає ланцюжок CWDH і повертає метрики за індексом гліфа."""
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
    """Читає ланцюжок CMAP і повертає мапу: codepoint -> glyph_index."""
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
        p += 2  # padding
        next_ofs, p = read_u32(buf, p, little)

        if mapping_method == 0:  # Direct
            char_offset, p = read_u16(buf, p, little)
            for cc in range(code_begin, code_end + 1):
                idx = cc - code_begin + char_offset
                if idx < 0xFFFF:
                    cmap[cc] = idx
        elif mapping_method == 1:  # Table
            for cc in range(code_begin, code_end + 1):
                idx = struct.unpack_from('<h' if little else '>h', buf, p)[0]
                p += 2
                if idx != -1:
                    cmap[cc] = idx
        elif mapping_method == 2:  # Scan
            count, p = read_u16(buf, p, little)
            if platform == 'NX':
                p += 2  # padding
                for _ in range(count):
                    cc, p = read_u32(buf, p, little)
                    idx = struct.unpack_from('<h' if little else '>h', buf, p)[0]; p += 2
                    p += 2  # padding
                    if idx != -1:
                        cmap[cc] = idx
            else:
                for _ in range(count):
                    cc, p = read_u16(buf, p, little)
                    idx = struct.unpack_from('<h' if little else '>h', buf, p)[0]; p += 2
                    if idx != -1:
                        cmap[cc] = idx
        else:
            raise ValueError('Невідомий метод CMAP: %d' % mapping_method)

        off = (next_ofs - 8) if next_ofs else 0
    return cmap


def unpack_bffnt(path: str, rotate180: bool = False, flip_y: bool = False) -> str:
    with open(path, 'rb') as f:
        buf = f.read()

    if len(buf) < 16:
        raise ValueError('Файл пошкоджений або порожній')

    sig = buf[0:4]
    if sig not in (b'FFNT', b'CFNT', b'RFNT', b'TNFR', b'RFNA'):
        raise ValueError('Невідома сигнатура: %r' % sig)

    little, version, header_size = detect_endian_and_version(buf, sig)
    platform = determine_platform(sig, little, version)

    # Знайдемо FINF (для метаданих) та TGLP (для аркушів)
    finf_off = find_section(buf, SIG_FINF)
    finf, offs = parse_finf(buf, finf_off, little, platform, version)

    # У FFNT офсети секцій зберігаються як (target+8), тому віднімаємо 8
    tglp_off = (offs['tglp'] - 8) if offs['tglp'] else find_section(buf, SIG_TGLP)
    cwdh_off = (offs['cwdh'] - 8) if offs['cwdh'] else find_section(buf, b'CWDH')
    cmap_off = (offs['cmap'] - 8) if offs['cmap'] else find_section(buf, b'CMAP')

    tglp, sheets = parse_tglp_and_extract(buf, tglp_off, little, platform, sig)
    widths_by_index = parse_cwdh_chain(buf, cwdh_off, little)
    code_to_index = parse_cmap_chain(buf, cmap_off, little, platform)

    # Папка для вивантаження поруч із вихідним файлом
    root = os.path.dirname(os.path.abspath(path))
    base = os.path.splitext(os.path.basename(path))[0]
    out_dir = os.path.join(root, base)
    os.makedirs(out_dir, exist_ok=True)

    meta: Dict[str, Any] = {
        'signature': sig.decode('ascii'),
        'bom': struct.unpack_from('>H', buf, 4)[0],
        'version': version,
        'header_size': header_size,
        'platform': platform,
        'source_file': os.path.basename(path),
        'finf': finf,
        'tglp': tglp,
    }

    # Побудуємо інформацію про гліфи та метрики
    row_count = int(tglp['rows'])
    col_count = int(tglp['cols'])
    per_sheet = row_count * col_count

    glyphs: List[Dict[str, Any]] = []
    for cc, idx in sorted(code_to_index.items(), key=lambda kv: kv[0]):
        sheet = idx // per_sheet
        rem = idx % per_sheet
        grid_x = rem % row_count
        grid_y = rem // row_count
        w = widths_by_index.get(idx)
        glyphs.append({
            'codepoint': f'U+{cc:04X}',
            'char': chr(cc) if 32 <= cc <= 0x10FFFF else '',
            'index': int(idx),
            'sheet': int(sheet),
            'grid_x': int(grid_x),
            'grid_y': int(grid_y),
            'width': w if w else None,
        })

    meta['glyphs'] = glyphs

    # Запис PNG аркушів (без .bin)
    sheet_png: List[str] = []
    for i, s in enumerate(sheets):
        suffix = ('.rot180' if rotate180 else '') + ('.flipY' if flip_y else '')
        png_name = f"sheet_{i}{suffix}.png"
        try:
            if tglp['format'] == 12:  # BC4_UNORM
                decode_sheet_to_png_bc4_gx2(
                    s,
                    int(tglp['sheet_width']),
                    int(tglp['sheet_height']),
                    os.path.join(out_dir, png_name),
                    i,
                    rotate180=rotate180,
                    flip_y=flip_y,
                )
                sheet_png.append(png_name)
            else:
                sheet_png.append('НЕ_ДЕКОДОВАНО: формат не підтримується поки що')
        except Exception as ex:
            sheet_png.append(f'НЕ_ДЕКОДОВАНО: {ex}')

    if sheet_png:
        meta['sheet_png'] = sheet_png

    # Щоб гарантувати ідентичне пакування без змін — збережемо весь вихідний файл у base64
    meta['file_b64'] = base64.b64encode(buf).decode('ascii')
    meta['png_ops'] = {
        'rotate180': bool(rotate180),
        'flipY': bool(flip_y),
    }

    with open(os.path.join(out_dir, 'font.json'), 'w', encoding='utf-8') as jf:
        json.dump(meta, jf, ensure_ascii=False, indent=2)

    # Приберемо застарілі .bin, якщо лишилися від попередніх запусків
    for fn in os.listdir(out_dir):
        if fn.lower().endswith('.bin') and fn.startswith('sheet_'):
            try:
                os.remove(os.path.join(out_dir, fn))
            except Exception:
                pass

    return out_dir


def _collect_bffnts(base: str, recursive: bool) -> list[str]:
    exts = ('.bffnt', '.bcfnt', '.brfnt')
    files: list[str] = []
    if os.path.isfile(base) and base.lower().endswith(exts):
        return [base]
    if not os.path.isdir(base):
        return files
    if recursive:
        for root, _, fns in os.walk(base):
            for fn in fns:
                if fn.lower().endswith(exts):
                    files.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(base):
            if fn.lower().endswith(exts):
                files.append(os.path.join(base, fn))
    return files


def main():
    # Прапорці трансформацій PNG
    rotate180 = False
    flip_y = False
    scan_all = False  # сканувати директорію замість одного файлу
    recursive = False
    args = sys.argv[1:]
    # Розбір прапорців (в довільному порядку)
    flags = {'--rotate180', '--flipY', '--all', '--r'}
    paths: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--rotate180':
            rotate180 = True
        elif a == '--flipY':
            flip_y = True
        elif a == '--all':
            scan_all = True
        elif a in ('--r', '-r', '--recursive'):
            recursive = True
        elif a.startswith('-') and a not in flags:
            print('Невідомий прапорець:', a, file=sys.stderr)
            return sys.exit(2)
        else:
            paths.append(a)
        i += 1

    # Визначимо які файли обробляти
    targets: list[str] = []
    if paths:
        for p in paths:
            targets.extend(_collect_bffnts(p, recursive))
    else:
        if scan_all:
            # поточна робоча тека
            targets = _collect_bffnts(os.getcwd(), recursive)
        else:
            # тека скрипта (поведінка за замовчуванням)
            here = os.path.dirname(os.path.abspath(__file__))
            targets = _collect_bffnts(here, recursive=False)

    if not targets:
        print('Не знайдено файлів *.bffnt/*.bcfnt/*.brfnt для розпакування')
        return sys.exit(0)

    ok = 0
    fail = 0
    for src in targets:
        try:
            out_dir = unpack_bffnt(src, rotate180=rotate180, flip_y=flip_y)
            print(f'OK: {os.path.basename(src)} → {out_dir}')
            ok += 1
        except Exception as ex:
            print(f'ПОМИЛКА у {os.path.basename(src)}: {ex}', file=sys.stderr)
            fail += 1
    print(f'Готово: успішно {ok}, помилок {fail}')


if __name__ == '__main__':
    # Режими:
    #  - без аргументів / із шляхом до .bffnt — розпакування
    #  - pack <тека_з_font.json> [вихід.bffnt] — пакування (оновлює метрики CWDH; PNG не перекодовуються)
    if len(sys.argv) >= 2 and sys.argv[1].lower() == 'pack':
        import hashlib
        folder = sys.argv[2] if len(sys.argv) >= 3 else os.path.join(os.path.dirname(__file__), 'CKingMain')
        font_json = os.path.join(folder, 'font.json')
        if not os.path.isfile(font_json):
            print('ПОМИЛКА: не знайдено font.json у теці', folder, file=sys.stderr)
            sys.exit(2)
        meta = json.load(open(font_json, 'r', encoding='utf-8'))
        file_b64 = meta.get('file_b64')
        if not file_b64:
            print('ПОМИЛКА: в font.json немає file_b64 для пакування', file=sys.stderr)
            sys.exit(3)
        raw = base64.b64decode(file_b64)

        # Спроба оновити CWDH метрики з JSON
        buf = bytearray(raw)
        patched_count = 0
        try:
            sig = raw[0:4]
            little, version, header_size = detect_endian_and_version(raw, sig)
            platform = determine_platform(sig, little, version)
            finf_off = find_section(raw, SIG_FINF)
            _finf, offs = parse_finf(raw, finf_off, little, platform, version)
            cwdh_off = (offs['cwdh'] - 8) if offs['cwdh'] else find_section(raw, b'CWDH')

            # мапа індекс -> (left, glyph, char)
            json_widths = {}
            for g in meta.get('glyphs', []):
                try:
                    idx = int(g.get('index'))
                except Exception:
                    continue
                w = g.get('width') or {}
                left = int(w.get('left', 0))
                glyphw = int(w.get('glyph', 0))
                charw = int(w.get('char', 0))
                if left < -128: left = -128
                if left > 127: left = 127
                if glyphw < 0: glyphw = 0
                if glyphw > 255: glyphw = 255
                if charw < 0: charw = 0
                if charw > 255: charw = 255
                json_widths[idx] = (left, glyphw, charw)

            off = cwdh_off
            visited = set()
            while off and off not in visited:
                visited.add(off)
                if raw[off:off+4] != b'CWDH':
                    raise ValueError('Очікував CWDH на офсеті 0x%X' % off)
                p = off + 4
                _sz, p = read_u32(raw, p, little)
                start_idx, p = read_u16(raw, p, little)
                end_idx, p = read_u16(raw, p, little)
                next_ofs, p = read_u32(raw, p, little)
                count = end_idx - start_idx + 1
                for i in range(count):
                    idx = start_idx + i
                    if idx in json_widths:
                        left, glyphw, charw = json_widths[idx]
                        buf[p + 0] = (left + 256) % 256
                        buf[p + 1] = glyphw & 0xFF
                        buf[p + 2] = charw & 0xFF
                        patched_count += 1
                    p += 3
                off = (next_ofs - 8) if next_ofs else 0
        except Exception as ex:
            print('ПОПЕРЕДЖЕННЯ: не вдалося оновити CWDH із JSON:', ex)

        # Перекодуємо PNG аркуші назад у BC4_UNORM + GX2-свізл та оновимо дані TGLP
        any_sheet_changed = False
        try:
            sig = raw[0:4]
            little, version, header_size = detect_endian_and_version(raw, sig)
            finf_off = find_section(raw, SIG_FINF)
            finf, offs = parse_finf(raw, finf_off, little, determine_platform(sig, little, version), version)
            tglp_off = (offs['tglp'] - 8) if offs['tglp'] else find_section(raw, SIG_TGLP)
            tglp, sheets = parse_tglp_and_extract(raw, tglp_off, little, determine_platform(sig, little, version), sig)
            names = meta.get('sheet_png', [])
            # Зчитаємо необхідні поля з TGLP ще раз для sheet_data_off
            p = tglp_off + 4
            _section_size, p = read_u32(raw, p, little)
            p += 2  # cell_w
            p += 2  # cell_h
            sheet_count = raw[p]; p += 1
            p += 1  # max_char_width
            sheet_size, p = read_u32(raw, p, little)
            p += 2  # base_line
            p += 2  # fmt
            p += 2  # rows
            p += 2  # cols
            sheet_w, p = read_u16(raw, p, little)
            sheet_h, p = read_u16(raw, p, little)
            sheet_data_off, p = read_u32(raw, p, little)

            for i in range(int(sheet_count)):
                expected_name = None
                for nm in names:
                    if nm.startswith(f'sheet_{i}') and nm.endswith('.png'):
                        expected_name = nm
                        break
                if expected_name is None:
                    continue
                pth = os.path.join(folder, expected_name)
                if not os.path.isfile(pth):
                    continue
                if not _HAS_PIL:
                    raise RuntimeError('Pillow потрібен для перекодування PNG у BC4')
                img_open = Image.open(pth)
                # Відкотити суфіксовані трансформації (як у верифікації)
                if '.rot180' in expected_name:
                    img_open = img_open.rotate(180)
                if '.flipY' in expected_name:
                    img_open = img_open.transpose(Image.FLIP_TOP_BOTTOM)
                swz = _encode_png_to_bc4_gx2(img_open, int(sheet_w), int(sheet_h), i)
                if len(swz) != int(sheet_size):
                    raise ValueError('Невірний розмір закодованого аркуша')
                pos = int(sheet_data_off) + i * int(sheet_size)
                buf[pos:pos+int(sheet_size)] = swz
                # Маркер змін — якщо пікселі відрізнялись від оригіналу
                try:
                    origL = _decode_sheet_pixels_bc4_gx2(sheets[i], int(sheet_w), int(sheet_h), i)
                    comp = (img_open.getchannel('A') if img_open.mode == 'RGBA' else img_open.convert('L'))
                    if comp.size == origL.size and list(comp.getdata()) != list(origL.getdata()):
                        any_sheet_changed = True
                except Exception:
                    any_sheet_changed = True
        except Exception as ex:
            print('ПОПЕРЕДЖЕННЯ: перекодування PNG не виконано:', ex)

        out_name = sys.argv[3] if len(sys.argv) >= 4 else meta.get('source_file', 'repacked.bffnt')
        out_path = out_name if os.path.isabs(out_name) else os.path.join(folder, out_name)
        with open(out_path, 'wb') as wf:
            wf.write(bytes(buf))
        h_raw = hashlib.sha256(raw).hexdigest()
        with open(out_path, 'rb') as rf:
            h_out = hashlib.sha256(rf.read()).hexdigest()
        print('SHA256 original(base64):', h_raw)
        print('SHA256 written:', h_out)
        if h_out == h_raw:
            print('УВАГА: Вихідний файл біт-ідентичний оригіналу (ймовірно, метрики/аркуші не змінені).')
        else:
            print(f'OK: Запаковано {out_path} (оновлено метрик: {patched_count}; перекодовано PNG: {"так" if any_sheet_changed else "ні"})')
    else:
        main()
