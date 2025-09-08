#!/usr/bin/env python3
import os
import sys
import json
import shutil
import tempfile
import subprocess

try:
    from PIL import Image
except Exception:
    Image = None


def _load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _normalize_meta(meta: dict) -> dict:
    # Drop fields that are expected to differ between pack/unpack
    drop = {'file_b64', 'source_file'}
    return {k: v for k, v in meta.items() if k not in drop}


def _images_pixel_equal(p1: str, p2: str) -> bool:
    if Image is None:
        # Fallback: compare file bytes (less robust)
        return open(p1, 'rb').read() == open(p2, 'rb').read()
    i1 = Image.open(p1)
    i2 = Image.open(p2)
    # Normalize mode for comparison
    if i1.mode != i2.mode:
        # Compare as RGBA when possible
        i1 = i1.convert('RGBA')
        i2 = i2.convert('RGBA')
    if i1.size != i2.size:
        return False
    return list(i1.getdata()) == list(i2.getdata())


def _run_cli(args, cwd=None):
    py = sys.executable or 'python'
    proc = subprocess.run([py] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


import unittest


class RoundtripTests(unittest.TestCase):
    def test_roundtrip_pack_unpack(self):
        # Source sample folder
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src_folder = os.path.join(repo_root, 'CKingMain')
        self.assertTrue(os.path.isdir(src_folder), 'CKingMain sample folder missing')

        with tempfile.TemporaryDirectory() as td:
            work_src = os.path.join(td, 'src')
            shutil.copytree(src_folder, work_src)

            out_bffnt = os.path.join(td, 'packed.bffnt')
            # Pack via CLI
            code, out = _run_cli(['bffnt.py', 'pack', work_src, out_bffnt], cwd=repo_root)
            self.assertEqual(code, 0, f'pack failed:\n{out}')
            self.assertTrue(os.path.isfile(out_bffnt), 'packed file not created')

            # Unpack the packed file via API for speed
            sys.path.insert(0, repo_root)
            import bffnt as bmod  # noqa
            # Use same PNG orientation settings as source
            src_meta0 = _load_json(os.path.join(work_src, 'font.json'))
            png_ops = src_meta0.get('png_ops') or {'rotate180': False, 'flipY': False}
            unpack_dir = bmod.unpack_bffnt(out_bffnt, rotate180=bool(png_ops.get('rotate180')), flip_y=bool(png_ops.get('flipY')))
            self.assertTrue(os.path.isdir(unpack_dir), 'unpack dir missing')

            # Compare font.json (normalized)
            src_meta = _normalize_meta(_load_json(os.path.join(work_src, 'font.json')))
            dst_meta = _normalize_meta(_load_json(os.path.join(unpack_dir, 'font.json')))
            # sheet_png order and names must be equal
            self.assertEqual(src_meta.get('sheet_png'), dst_meta.get('sheet_png'), 'sheet list differs')
            # Compare selected meta fields
            for key in ('signature', 'platform', 'finf', 'tglp'):
                self.assertEqual(src_meta.get(key), dst_meta.get(key), f'meta field {key} differs')

            # Compare all PNGs to decoding of actual packed file sheets
            import bffnt as bmod2
            with open(out_bffnt, 'rb') as rf:
                buf = rf.read()
            sig = buf[0:4]
            little, version, header_size = bmod2.detect_endian_and_version(buf, sig)
            tglp_off = bmod2.find_section(buf, bmod2.SIG_TGLP)
            tglp2, sheets = bmod2.parse_tglp_and_extract(buf, tglp_off, little, bmod2.determine_platform(sig, little, version), sig)
            w = int(tglp2['sheet_width']); h = int(tglp2['sheet_height'])
            bw = w // 4; bh = h // 4
            # Sample blocks on a coarse grid to keep tests fast
            step_blocks = 16  # 16*4 = 64px stride
            ops = src_meta0.get('png_ops') or {'rotate180': False, 'flipY': False}
            rot = bool(ops.get('rotate180'))
            flip = bool(ops.get('flipY'))
            for i, name in enumerate(src_meta.get('sheet_png', [])):
                p2 = os.path.join(unpack_dir, dst_meta['sheet_png'][i])
                self.assertTrue(os.path.isfile(p2), f'missing PNG: {name}')
                if Image is None:
                    self.assertTrue(os.path.getsize(p2) > 0, 'empty PNG')
                    continue
                img_dst = Image.open(p2)
                dst = (img_dst.getchannel('A') if img_dst.mode == 'RGBA' else img_dst.convert('L'))
                dst_pix = dst.load()
                data = sheets[i]
                for by in range(0, bh, step_blocks):
                    for bx in range(0, bw, step_blocks):
                        off = bmod2._addr_from_coord_macrotiled_bc4(bx, by, bw, bh, 0, i & 3)
                        block = data[off:off+8]
                        vals = bmod2._decode_bc4_block(block)
                        # Compare 4x4 pixels
                        for py in range(4):
                            for px in range(4):
                                xs = bx*4 + px
                                ys = by*4 + py
                                # Map source coords to destination coords considering png_ops
                                if not rot and not flip:
                                    xd, yd = xs, ys
                                elif flip and not rot:
                                    xd, yd = xs, h - 1 - ys
                                elif rot and not flip:
                                    xd, yd = w - 1 - xs, h - 1 - ys
                                else:  # rot and flip
                                    xd, yd = w - 1 - xs, ys
                                self.assertEqual(int(vals[py*4+px]), int(dst_pix[xd, yd]), f'Pixel mismatch at sheet {i} ({xd},{yd})')


if __name__ == '__main__':
    unittest.main(verbosity=2)
