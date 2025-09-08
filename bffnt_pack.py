#!/usr/bin/env python3
"""Pack routines: build a BFFNT from a JSON + PNG sheets folder."""
import os
import sys
import json
import struct
import base64
import hashlib
from typing import Dict, Any

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
    read_u16,
    read_u32,
    _decode_bc4_block,
    _deswizzle_bc4_gx2_blocks,
    _encode_png_to_bc4_gx2,
)

try:
    from PIL import Image  # noqa: F401
except Exception:
    Image = None


def _parse_cp(s):
    if s is None:
        return None
    if isinstance(s, int):
        return int(s)
    ss = str(s).strip().upper()
    try:
        if ss.startswith('U+'):
            return int(ss[2:], 16)
        if ss.startswith('0X'):
            return int(ss, 16)
        return int(ss, 10)
    except Exception:
        return None


def pack_from_json_folder(folder: str, out_path: str = None, verbose: bool = False) -> str:
    font_json = os.path.join(folder, 'font.json')
    if not os.path.isfile(font_json):
        print('ПОМИЛКА: не знайдено font.json у теці', folder, file=sys.stderr)
        raise SystemExit(2)
    print('[PACK] Використовую font.json:', font_json)
    # Load font.json with a small robustness shim: fix a known typo pattern
    # seen in some assets ("c:har" "Ґ" -> "char": "Ґ").
    with open(font_json, 'r', encoding='utf-8', errors='strict') as jf:
        txt = jf.read()
    try:
        meta = json.loads(txt)
    except Exception:
        fixed = txt.replace('"c:har" "', '"char": "')
        if fixed != txt:
            try:
                meta = json.loads(fixed)
                print('[PACK] Поправлено некоректний ключ у font.json ("c:har" → "char")')
            except Exception:
                print('ПОМИЛКА: font.json пошкоджений і не вдалося виправити автоматично', file=sys.stderr)
                raise
        else:
            print('ПОМИЛКА: font.json має синтаксичну помилку', file=sys.stderr)
            raise
    # Allow forcing packer to ignore embedded base64 via JSON flag
    ignore_b64 = bool(meta.get('ignore_file_b64'))
    file_b64 = (None if ignore_b64 else meta.get('file_b64'))
    # Verbosity control: CLI flag takes precedence, then JSON, then env var
    verbose = bool(verbose) or bool(meta.get('verbose_logs')) or bool(os.environ.get('BFFNT_VERBOSE'))
    raw = None
    if file_b64:
        raw = base64.b64decode(file_b64)
    else:
        # Fallback: use original source file next to unpacked folder
        parent = os.path.abspath(os.path.join(folder, os.pardir))
        # Prefer the exact name from JSON if present; otherwise try by folder basename + known extensions
        src_name = (meta.get('source_file') or '').strip()
        candidates = []
        if src_name:
            candidates.append(os.path.join(parent, src_name))
        base = os.path.basename(os.path.normpath(folder))
        for ext in ('.bffnt', '.bcfnt', '.brfnt'):
            candidates.append(os.path.join(parent, base + ext))
        for cand in candidates:
            if os.path.isfile(cand):
                with open(cand, 'rb') as rf:
                    raw = rf.read()
                break
        if raw is None:
            print('ПОМИЛКА: в font.json немає file_b64 для пакування і не знайдено оригінальний файл', file=sys.stderr)
            raise SystemExit(3)

    buf = bytearray(raw)
    patched_count = 0
    cmap_updated_pairs = 0

    try:
        sig = raw[0:4]
        little, version, header_size = detect_endian_and_version(raw, sig)
        platform = determine_platform(sig, little, version)
        finf_off = find_section(raw, SIG_FINF)
        _finf, offs = parse_finf(raw, finf_off, little, platform, version)
        cwdh_off = (offs['cwdh'] - 8) if offs['cwdh'] else find_section(raw, b'CWDH')
        cmap_off = (offs['cmap'] - 8) if offs['cmap'] else find_section(raw, b'CMAP')
        print('[PACK] Формат:', platform, 'Endian:', 'LE' if little else 'BE')
        print('[PACK] CWDH offset: 0x%X' % cwdh_off)

        # Best-effort: apply FINF header values from JSON (if present) so that
        # changes in font.json influence the repacked binary beyond just widths.
        # This does not relocate sections; only in-place field updates are done.
        finf_meta = meta.get('finf') or {}
        try:
            # FINF layout mirrors parse_finf()
            p = finf_off + 4
            _sz, p = read_u32(raw, p, little)
            base = p
            def _u8(v):
                return int(max(0, min(255, int(v))))
            def _u16(v):
                return int(max(0, min(0xFFFF, int(v))))
            if (platform in ('Ctr',) and version < 0x04000000):
                # Offsets relative to base for old Ctr variant
                mapping = (
                    ('type', 0, 'u8'),
                    ('line_feed', 1, 'u8'),
                    ('alter_char_index', 2, 'u16'),
                    ('default_left', 4, 'u8'),
                    ('default_glyph', 5, 'u8'),
                    ('default_char', 6, 'u8'),
                    ('char_encoding', 7, 'u8'),
                    # tglp/cwdh/cmap ptrs follow
                    ('height', 20, 'u8'),
                    ('width', 21, 'u8'),
                    ('ascent', 22, 'u8'),
                )
                for key, rel, kind in mapping:
                    if key in finf_meta:
                        if kind == 'u8':
                            buf[base + rel] = _u8(finf_meta[key])
                        elif kind == 'u16':
                            struct.pack_into('<H' if little else '>H', buf, base + rel, _u16(finf_meta[key]))
            else:
                # Cafe/NX/modern layout
                mapping = (
                    ('type', 0, 'u8'),
                    ('height', 1, 'u8'),
                    ('width', 2, 'u8'),
                    ('ascent', 3, 'u8'),
                    ('line_feed', 4, 'u16'),
                    ('alter_char_index', 6, 'u16'),
                    ('default_left', 8, 'u8'),
                    ('default_glyph', 9, 'u8'),
                    ('default_char', 10, 'u8'),
                    ('char_encoding', 11, 'u8'),
                    # tglp/cwdh/cmap ptrs follow and are not modified
                )
                for key, rel, kind in mapping:
                    if key in finf_meta:
                        if kind == 'u8':
                            buf[base + rel] = _u8(finf_meta[key])
                        elif kind == 'u16':
                            struct.pack_into('<H' if little else '>H', buf, base + rel, _u16(finf_meta[key]))
        except Exception as e:
            print('[PACK] ПОПЕРЕДЖЕННЯ: не вдалося застосувати FINF з JSON:', e)

        # widths from JSON + quick glyph info map (for verbose logging)
        json_widths: Dict[int, Any] = {}
        glyph_info_by_idx: Dict[int, Dict[str, Any]] = {}
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
            glyph_info_by_idx[idx] = {
                'char': (g.get('char') or ''),
                'codepoint': (_parse_cp(g.get('codepoint'))),
            }
        if meta.get('glyphs'):
            print(f"[PACK] JSON glyphs: {len(meta['glyphs'])}; widths specified for {len(json_widths)} indexes")
        else:
            print('[PACK] УВАГА: У font.json відсутній масив glyphs — CWDH/CMAP не будуть оновлені')

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
                left, glyphw, charw = json_widths.get(idx, (None, None, None))
                if left is None:
                    p += 3
                    continue
                orig_left = left
                if left < 0:
                    left = (left + 256) & 0xFF
                else:
                    left &= 0xFF
                buf[p] = left & 0xFF
                buf[p+1] = glyphw & 0xFF
                buf[p+2] = charw & 0xFF
                p += 3
                patched_count += 1
                if verbose:
                    info = glyph_info_by_idx.get(idx, {})
                    cp = info.get('codepoint')
                    ch = info.get('char')
                    cp_txt = (f"U+{cp:04X}" if isinstance(cp, int) and cp >= 0 else "?")
                    try:
                        ch_disp = ch if ch else (chr(cp) if isinstance(cp, int) and 0 <= cp <= 0x10FFFF else '')
                    except Exception:
                        ch_disp = ch or ''
                    print(f"[PACK] CWDH: idx {idx} '{ch_disp}' {cp_txt} -> left={orig_left} glyph={glyphw} char={charw}")
            off = (next_ofs - 8) if next_ofs else 0
        print('[PACK] Оновлено метрик CWDH:', patched_count)

        # desired codepoint->index from JSON
        desired_map = {}
        for g in meta.get('glyphs', []):
            cp = _parse_cp(g.get('codepoint'))
            if cp is None:
                # Fallback: try infer from single-character 'char' field
                ch = g.get('char')
                if isinstance(ch, str) and len(ch) > 0:
                    try:
                        cp = ord(ch[0])
                    except Exception:
                        cp = None
            if cp in (0xFFFF,):
                continue
            try:
                idx = int(g.get('index'))
            except Exception:
                idx = None
            if cp is None or idx is None:
                continue
            desired_map[cp] = idx
        if desired_map:
            print(f"[PACK] JSON requested CMAP pairs: {len(desired_map)}")

        def _pack_i16(val: int) -> bytes:
            return struct.pack('<h' if little else '>h', int(val))

        def _pack_u16(val: int) -> bytes:
            return struct.pack('<H' if little else '>H', int(val))

        off = cmap_off
        visited = set()
        seg_no = 0
        while off and off not in visited:
            visited.add(off)
            if raw[off:off+4] != b'CMAP':
                raise ValueError('Очікував CMAP на офсеті 0x%X' % off)
            p = off + 4
            section_size, p = read_u32(raw, p, little)
            if platform == 'NX':
                code_begin, p = read_u32(raw, p, little)
                code_end, p = read_u32(raw, p, little)
            else:
                code_begin, p = read_u16(raw, p, little)
                code_end, p = read_u16(raw, p, little)
            mapping_method, p = read_u16(raw, p, little)
            p += 2
            next_ofs, p = read_u32(raw, p, little)

            seg_no += 1
            try:
                if mapping_method == 1:  # Table
                    cc = code_begin
                    while cc <= code_end:
                        idx = desired_map.get(cc, -1)
                        buf[p:p+2] = _pack_i16(idx if -32768 <= idx <= 32767 else -1)
                        p += 2
                        if idx != -1:
                            cmap_updated_pairs += 1
                        cc += 1
                elif mapping_method == 2:  # Scan
                    count, p2 = read_u16(raw, p, little)
                    if platform == 'NX':
                        p2 += 2
                        for _ in range(count):
                            cp, p2 = read_u32(raw, p2, little)
                            idx_off = p2
                            idx_old, p2 = read_u16(raw, p2, little)
                            p2 += 2
                            new_idx = desired_map.get(cp, idx_old)
                            buf[idx_off:idx_off+2] = _pack_i16(new_idx if -32768 <= new_idx <= 32767 else -1)
                            if new_idx != idx_old:
                                cmap_updated_pairs += 1
                    else:
                        p2 = p
                        for _ in range(count):
                            cp, p2 = read_u16(raw, p2, little)
                            idx_off = p2
                            idx_old, p2 = read_u16(raw, p2, little)
                            new_idx = desired_map.get(cp, idx_old)
                            buf[idx_off:idx_off+2] = _pack_i16(new_idx if -32768 <= new_idx <= 32767 else -1)
                            if new_idx != idx_old:
                                cmap_updated_pairs += 1
                elif mapping_method == 0:  # Direct
                    char_offset, _tmp = read_u16(raw, p, little)
                    ok = True
                    new_off = None
                    for i, cc in enumerate(range(code_begin, code_end + 1)):
                        idx = desired_map.get(cc, None)
                        if idx is None:
                            ok = False; break
                        expect_off = idx - (cc - code_begin)
                        if new_off is None:
                            new_off = expect_off
                        elif expect_off != new_off:
                            ok = False; break
                    if ok and new_off is not None and 0 <= new_off <= 0xFFFF:
                        buf[p:p+2] = _pack_u16(new_off)
                        cmap_updated_pairs += (code_end - code_begin + 1)
                    else:
                        print(f'[PACK] CMAP seg#{seg_no} Direct: неможливо відобразити довільні зміни — пропускаю')
                else:
                    print(f'[PACK] CMAP seg#{seg_no}: невідомий метод {mapping_method} — пропускаю')
            except Exception as e:
                print(f'[PACK] CMAP seg#{seg_no}: помилка оновлення: {e}')

            off = (next_ofs - 8) if next_ofs else 0
        print('[PACK] Оновлено CMAP пар:', cmap_updated_pairs)

        # Append SCAN override from JSON (write all pairs from JSON)
        try:
            idx_to_cp = {}
            for g in meta.get('glyphs', []):
                try:
                    idx = int(g.get('index'))
                except Exception:
                    continue
                cp = _parse_cp(g.get('codepoint'))
                if cp is None:
                    continue
                idx_to_cp[idx] = cp
            # Strictly respect JSON: add all pairs as an override segment so they take precedence
            override_pairs = []
            for cp, idx in desired_map.items():
                if platform != 'NX' and (cp is None or cp < 0 or cp > 0xFFFF):
                    continue
                override_pairs.append((cp, idx))
            if override_pairs:
                print(f"[PACK] CMAP override (JSON pairs): {len(override_pairs)} (first 5: {override_pairs[:5]})")
                # Insert new segment as head of the CMAP chain: set its next->old_first,
                # then update FINF's cmap pointer to point to the new head.
                seg_start = len(buf)
                seg = bytearray()
                seg += b'CMAP'
                if platform == 'NX':
                    count = len(override_pairs)
                    section_size = 24 + 2 + 2 + count * (4 + 2 + 2)
                    seg += struct.pack('<I' if little else '>I', section_size)
                    seg += struct.pack('<I' if little else '>I', 0)  # code_begin (unused for scan)
                    seg += struct.pack('<I' if little else '>I', 0)  # code_end
                    seg += struct.pack('<H' if little else '>H', 2)  # mapping_method=SCAN
                    seg += b'\x00\x00'
                    seg += struct.pack('<I' if little else '>I', 0)  # next_ofs -> 0 (replace entire chain)
                    seg += struct.pack('<H' if little else '>H', count)
                    seg += b'\x00\x00'
                    for cp, idx in override_pairs:
                        seg += struct.pack('<I' if little else '>I', int(cp))
                        seg += struct.pack('<h' if little else '>h', int(idx))
                        seg += b'\x00\x00'
                else:
                    count = len(override_pairs)
                    section_size = 20 + 2 + count * 4
                    seg += struct.pack('<I' if little else '>I', section_size)
                    seg += struct.pack('<H' if little else '>H', 0)  # code_begin
                    seg += struct.pack('<H' if little else '>H', 0)  # code_end
                    seg += struct.pack('<H' if little else '>H', 2)  # mapping_method=SCAN
                    seg += b'\x00\x00'
                    seg += struct.pack('<I' if little else '>I', 0)  # next_ofs -> 0 (replace entire chain)
                    seg += struct.pack('<H' if little else '>H', count)
                    for cp, idx in override_pairs:
                        seg += struct.pack('<H' if little else '>H', int(cp) & 0xFFFF)
                        seg += struct.pack('<h' if little else '>h', int(idx) & 0xFFFF)
                buf.extend(seg)
                new_head_target = seg_start + 8

                # Update FINF cmap pointer to new head
                try:
                    pp = finf_off + 4
                    _sz, pp = read_u32(buf, pp, little)
                    if (platform in ('Ctr',) and version < 0x04000000):
                        # old Ctr layout: tglp/cwdh/cmap at offsets pp+? — recompute by stepping
                        # type..char_encoding took 8 bytes starting from pp
                        # Fields:
                        #  0:type 1:line_feed 2..3:alter_char_index 4:default_left 5:default_glyph 6:default_char 7:char_encoding
                        pp2 = pp + 8
                        # tglp, cwdh, cmap
                        pos_tglp = pp2
                        pos_cwdh = pp2 + 4
                        pos_cmap = pp2 + 8
                    else:
                        # Cafe/NX layout: after type(1),height(1),width(1),ascent(1),line_feed(2),alter(2),default_left(1),default_glyph(1),default_char(1),char_encoding(1)
                        # that's 1+1+1+1+2+2+1+1+1+1 = 12 bytes
                        pos_tglp = pp + 12
                        pos_cwdh = pos_tglp + 4
                        pos_cmap = pos_tglp + 8
                    struct.pack_into('<I' if little else '>I', buf, pos_cmap, int(new_head_target))
                    print(f"[PACK] FINF: cmap pointer -> 0x{new_head_target:X} (new head)")
                except Exception as e:
                    print('[PACK] ПОПЕРЕДЖЕННЯ: не вдалося оновити FINF.cmap → новий head:', e)
                print(f"[PACK] Додано CMAP Scan override на {len(override_pairs)} пар(и) як head (ланцюжок CMAP замінено)")
        except Exception as ex:
            print('[PACK] Попередження: не вдалося додати CMAP Scan override:', ex)

    except Exception as ex:
        print('ПОПЕРЕДЖЕННЯ: не вдалося оновити CWDH із JSON:', ex)

    # Re-encode PNG sheets back to BC4 and update data
    any_sheet_changed = False
    try:
        sig = raw[0:4]
        little, version, header_size = detect_endian_and_version(raw, sig)
        # Parse from the patched buffer (buf), not original raw
        finf_off = find_section(buf, SIG_FINF)
        finf, offs = parse_finf(buf, finf_off, little, determine_platform(sig, little, version), version)
        tglp_off = (offs['tglp'] - 8) if offs['tglp'] else find_section(buf, SIG_TGLP)
        tglp, sheets = parse_tglp_and_extract(buf, tglp_off, little, determine_platform(sig, little, version), sig)
        names = meta.get('sheet_png', [])
        png_ops = meta.get('png_ops') or {'rotate180': False, 'flipY': False}
        sheet_count = int(tglp.get('sheet_count', len(sheets)))
        sheet_size = int(tglp.get('sheet_size', len(sheets[0]) if sheets else 0))
        sheet_w = int(tglp.get('sheet_width', 0))
        sheet_h = int(tglp.get('sheet_height', 0))

        # Apply safe TGLP header fields from JSON (non-structural, no relocation)
        try:
            tglp_meta = meta.get('tglp') or {}
            base = tglp_off + 8  # after 'TGLP'(4) + section_size(4)
            def _u8(v):
                return int(max(0, min(255, int(v))))
            def _u16(v):
                return int(max(0, min(0xFFFF, int(v))))
            patched_tglp = 0
            if 'cell_width' in tglp_meta:
                buf[base + 0] = _u8(tglp_meta['cell_width']); patched_tglp += 1
            if 'cell_height' in tglp_meta:
                buf[base + 1] = _u8(tglp_meta['cell_height']); patched_tglp += 1
            if 'max_char_width' in tglp_meta:
                buf[base + 3] = _u8(tglp_meta['max_char_width']); patched_tglp += 1
            if 'base_line' in tglp_meta:
                struct.pack_into('<H' if little else '>H', buf, base + 8, _u16(tglp_meta['base_line'])); patched_tglp += 1
            if patched_tglp:
                print(f"[PACK] Оновлено TGLP (безпечні поля): {patched_tglp}")
        except Exception as e:
            print('[PACK] ПОПЕРЕДЖЕННЯ: не вдалося застосувати TGLP з JSON (безпечні поля):', e)

        def _locate_sheet_data_off(raw_bytes: bytes, sheet_bytes_list: list, hint_start: int) -> int:
            if not sheet_bytes_list:
                return 0
            s0 = sheet_bytes_list[0]
            pos = raw_bytes.find(s0, max(0, hint_start))
            if pos >= 0:
                return pos
            return raw_bytes.find(s0)

        sheet_data_off = _locate_sheet_data_off(raw, sheets, tglp_off)
        print('[PACK] TGLP sheets:', sheet_count, 'sheet_size:', sheet_size, 'bytes; size:', sheet_w, 'x', sheet_h)

        def _find_sheet_path(i: int):
            for nm in names:
                if nm.startswith(f'sheet_{i}') and nm.endswith('.png'):
                    pth = os.path.join(folder, nm)
                    if os.path.isfile(pth):
                        return nm, pth
            candidates = [
                f'sheet_{i}.png',
                f'sheet_{i}.flipY.png',
                f'sheet_{i}.rot180.png',
                f'sheet_{i}.rot180.flipY.png',
                f'sheet_{i}.flipY.rot180.png',
            ]
            for nm in candidates:
                pth = os.path.join(folder, nm)
                if os.path.isfile(pth):
                    return nm, pth
            return None, None

        for i in range(int(sheet_count)):
            nm, pth = _find_sheet_path(i)
            if not pth:
                print(f'[PACK] sheet {i}: PNG не знайдено — залишаю оригінал')
                continue
            if not _HAS_PIL:
                raise RuntimeError('Pillow потрібен для перекодування PNG у BC4')
            print(f'[PACK] sheet {i}: PNG = {pth}')
            img_open = Image.open(pth)
            rot_flag = ('.rot180' in (nm or '')) or bool(png_ops.get('rotate180'))
            flip_flag = ('.flipY' in (nm or '')) or bool(png_ops.get('flipY'))
            if rot_flag:
                img_open = img_open.rotate(180)
            if flip_flag:
                img_open = img_open.transpose(Image.FLIP_TOP_BOTTOM)
            # Compare with original
            try:
                # Build grayscale from original sheet bytes
                bw = sheet_w // 4; bh = sheet_h // 4
                lin_blocks = _deswizzle_bc4_gx2_blocks(sheets[i], bw, bh, i)
                origL = Image.new('L', (sheet_w, sheet_h))
                pix = origL.load(); off2 = 0
                for by in range(bh):
                    for bx in range(bw):
                        block = lin_blocks[off2:off2+8]; off2 += 8
                        vals = _decode_bc4_block(block)
                        for py in range(4):
                            for px in range(4):
                                x = bx * 4 + px
                                y = by * 4 + py
                                pix[x, y] = vals[py*4+px]
            except Exception:
                origL = None
            comp = (img_open.getchannel('A') if img_open.mode == 'RGBA' else img_open.convert('L'))
            equal = (origL is not None and comp.size == origL.size and list(comp.getdata()) == list(origL.getdata()))
            if equal:
                print(f'[PACK] sheet {i}: без змін (пікселі збігаються з оригіналом)')
            else:
                pos = int(sheet_data_off) + i * int(sheet_size)
                print(f'[PACK] sheet {i}: змінено → кодуємо BC4, запис: offset=0x%X size=%d' % (pos, int(sheet_size)))
                swz = _encode_png_to_bc4_gx2(img_open, int(sheet_w), int(sheet_h), i)
                if len(swz) != int(sheet_size):
                    raise ValueError('Невірний розмір закодованого аркуша')
                buf[pos:pos+int(sheet_size)] = swz
                any_sheet_changed = True
    except Exception as ex:
        print('ПОПЕРЕДЖЕННЯ: перекодування PNG не виконано:', ex)

    if out_path is None:
        out_name = meta.get('source_file', 'repacked.bffnt')
        out_path = os.path.join(folder, out_name)
    try:
        with open(out_path, 'wb') as wf:
            wf.write(bytes(buf))
    except PermissionError:
        # Try to remove existing file (may be read-only or locked)
        try:
            if os.path.isfile(out_path):
                os.remove(out_path)
            with open(out_path, 'wb') as wf:
                wf.write(bytes(buf))
        except Exception as e:
            print(f"ПОМИЛКА: не вдалося записати {out_path}. Закрийте файли у сторонніх програмах (наприклад, Switch Toolbox/переглядач) і спробуйте ще раз. Деталі: {e}", file=sys.stderr)
            raise
    h_raw = hashlib.sha256(raw).hexdigest()
    with open(out_path, 'rb') as rf:
        h_out = hashlib.sha256(rf.read()).hexdigest()
    print('[PACK] SHA256 original(base64):', h_raw)
    print('[PACK] SHA256 written:', h_out)
    if h_out == h_raw:
        print('[PACK] УВАГА: Вихідний файл біт-ідентичний оригіналу (ймовірно, метрики/аркуші не змінені або не знайдено змінені PNG).')
    else:
        print(f'[PACK] OK: Запаковано {out_path} (оновлено метрик: {patched_count}; перекодовано PNG: {"так" if any_sheet_changed else "ні"})')
    return out_path
