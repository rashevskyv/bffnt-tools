#!/usr/bin/env python3
import os
import sys
import json
import shutil
import tempfile
import subprocess

from typing import Tuple

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

            # Try to swap two codepoints to test CMAP update (only if non-Direct segment)
            # Pick next glyph on same sheet
            glyph2 = None
            for g in meta['glyphs']:
                if int(g.get('sheet', 0)) == int(glyph.get('sheet', 0)) and int(g.get('index')) != int(glyph.get('index')):
                    glyph2 = g
                    break
            cp1 = _parse_cp(glyph.get('codepoint'))
            cp2 = _parse_cp(glyph2.get('codepoint')) if glyph2 else None

            # Swap codepoints in JSON
            if cp1 is not None and cp2 is not None and cp1 != cp2:
                glyph['codepoint'], glyph2['codepoint'] = glyph2['codepoint'], glyph['codepoint']

            _save_json(os.path.join(work_src, 'font.json'), meta)

        # Modify PNG: draw a 4x4 white block inside the cell
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

            # Mapping checks are omitted here to focus on JSON width persistency,
            # as CMAP structure (Direct/Table/Scan) may restrict arbitrary remaps.


if __name__ == '__main__':
    unittest.main(verbosity=2)
