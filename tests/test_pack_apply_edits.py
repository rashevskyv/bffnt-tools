#!/usr/bin/env python3
import os
import sys
import json
import shutil
import tempfile
import subprocess

from typing import Tuple

try:
    from PIL import Image
except Exception:
    Image = None


def _run_cli(args, cwd=None) -> Tuple[int, str]:
    py = sys.executable or 'python'
    proc = subprocess.run([py] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def _load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_json(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _find_png(base_dir: str, i: int) -> str:
    # Prefer names typical to repo
    for nm in (
        f'sheet_{i}.flipY.png',
        f'sheet_{i}.rot180.png',
        f'sheet_{i}.png',
    ):
        p = os.path.join(base_dir, nm)
        if os.path.isfile(p):
            return p
    # fallback: scan
    for fn in os.listdir(base_dir):
        if fn.startswith(f'sheet_{i}') and fn.endswith('.png'):
            return os.path.join(base_dir, fn)
    raise FileNotFoundError('PNG for sheet %d not found' % i)


def _edit_png_cell_block(png_path: str, meta: dict, glyph: dict):
    assert Image is not None, 'Pillow required for PNG edit test'
    img = Image.open(png_path)
    # Work in RGBA to write alpha, or L for grayscale
    rgba = (img.mode == 'RGBA')
    if not rgba:
        img = img.convert('L')
    cw = int(meta['tglp']['cell_width'])
    ch = int(meta['tglp']['cell_height'])
    real_w = cw + 1
    real_h = ch + 1
    gx = int(glyph['grid_x'])
    gy = int(glyph['grid_y'])
    x0 = gx * real_w + 1
    y0 = gy * real_h + 1
    # Choose a 4x4 block inside the cell, aligned to 4 for BC4
    def align_block(a0, a1, size):
        b = a0
        while b % size != 0:
            b += 1
        if b + size - 1 >= a1:
            b = max(a0, a1 - size)
        return b
    bx = align_block(x0, x0 + cw, 4)
    by = align_block(y0, y0 + ch, 4)
    pix = img.load()
    for dy in range(4):
        for dx in range(4):
            x = bx + dx
            y = by + dy
            if rgba:
                r, g, b, a = pix[x, y]
                pix[x, y] = (255, 255, 255, 255)
            else:
                pix[x, y] = 255
    img.save(png_path)
    return (bx, by)


import unittest


class PackApplyEditsTests(unittest.TestCase):
    def test_pack_reflects_edits(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src_folder = os.path.join(repo_root, 'CKingMain')
        assert os.path.isdir(src_folder)

        with tempfile.TemporaryDirectory() as td:
            work_src = os.path.join(td, 'src')
            shutil.copytree(src_folder, work_src)

            meta = _load_json(os.path.join(work_src, 'font.json'))
        # Find a glyph on sheet 0 to edit
            glyph = None
            for g in meta['glyphs']:
                if int(g.get('sheet', 0)) == 0:
                    glyph = g
                    break
            assert glyph is not None

        # Modify widths (keep constraints)
            w = glyph.get('width') or {'left': 0, 'glyph': 0, 'char': 0}
            left = int(w.get('left', 0)) + 1
            glyphw = max(left, int(w.get('glyph', 0)))
            charw = min(glyphw, int(w.get('char', 0)))
            glyph['width'] = {'left': left, 'glyph': glyphw, 'char': charw}
            _save_json(os.path.join(work_src, 'font.json'), meta)

        # Modify PNG: draw a 4x4 white block inside the cell
            png0 = _find_png(work_src, 0)
            bx, by = _edit_png_cell_block(png0, meta, glyph)

        # Pack
            out_bffnt = os.path.join(td, 'packed.bffnt')
            code, out = _run_cli(['bffnt.py', 'pack', work_src, out_bffnt], cwd=repo_root)
            self.assertEqual(code, 0, f'pack failed:\n{out}')
            self.assertTrue(os.path.isfile(out_bffnt))

        # Unpack packed
            sys.path.insert(0, repo_root)
            import bffnt as bmod
            unpack_dir = bmod.unpack_bffnt(out_bffnt, rotate180=bool((meta.get('png_ops') or {}).get('rotate180')), flip_y=bool((meta.get('png_ops') or {}).get('flipY')))

        # Verify widths
            out_meta = _load_json(os.path.join(unpack_dir, 'font.json'))
            idx = int(glyph['index'])
            got = None
            for g in out_meta['glyphs']:
                if int(g.get('index', -1)) == idx:
                    got = g
                    break
            self.assertIsNotNone(got, 'updated glyph not found in unpacked font.json')
            self.assertEqual(got.get('width'), glyph['width'], 'widths not updated in repacked file')

        # Verify PNG pixel edited survived re-pack
            out_png0 = _find_png(unpack_dir, 0)
            self.assertTrue(os.path.isfile(out_png0))
            if Image is not None:
                im2 = Image.open(out_png0)
                comp = im2.getchannel('A') if im2.mode == 'RGBA' else im2.convert('L')
                px = comp.load()
                # The same (bx,by) coordinates should be white (255) in flipY image as well
                self.assertEqual(int(px[bx, by]), 255, 'edited PNG block not reflected after pack/unpack')


if __name__ == '__main__':
    unittest.main(verbosity=2)
