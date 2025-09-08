#!/usr/bin/env python3
import os
import sys
import shutil
import hashlib
import unittest


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _walk_hashes(root_dir):
    pairs = {}
    for dirpath, _, filenames in os.walk(root_dir):
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root_dir)
            pairs[rel] = _sha256(p)
    return pairs


class RoundtripCkingMainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.fonts = os.path.join(cls.repo, 'fonts')
        # Handle filename with or without space before _Test
        candidates = ['CKingMain_Test.bffnt', 'CKingMain _Test.bffnt']
        cls.src_bffnt = None
        for c in candidates:
            p = os.path.join(cls.fonts, c)
            if os.path.isfile(p):
                cls.src_bffnt = p
                break
        if cls.src_bffnt is None:
            raise unittest.SkipTest('Test asset CKingMain_Test.bffnt not found in fonts/')

        base = os.path.splitext(os.path.basename(cls.src_bffnt))[0]
        cls.unpack_dir_1 = os.path.join(cls.fonts, base)
        cls.packed_bffnt = os.path.join(cls.fonts, 'CKingMain_Test_packed.bffnt')
        cls.unpack_dir_2 = os.path.join(cls.fonts, 'CKingMain_Test_packed')

        # Clean any leftovers
        for p in [cls.unpack_dir_1, cls.unpack_dir_2]:
            if os.path.isdir(p):
                shutil.rmtree(p)
        if os.path.isfile(cls.packed_bffnt):
            os.remove(cls.packed_bffnt)

        # Import module under test
        sys.path.insert(0, cls.repo)
        import bffnt_unpack as u
        import bffnt_pack as p
        cls.u = u
        cls.p = p

        # Unpack step for use in tests
        cls.u.unpack_bffnt(cls.src_bffnt)

    @classmethod
    def tearDownClass(cls):
        # Cleanup artifacts
        for p in [cls.unpack_dir_1, cls.unpack_dir_2, cls.packed_bffnt]:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                elif os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass

    def test_01_unpack_ckingmain_test(self):
        # Verify unpack directory exists and contains expected core files
        self.assertTrue(os.path.isdir(self.unpack_dir_1), 'Unpack dir missing')
        self.assertTrue(os.path.isfile(os.path.join(self.unpack_dir_1, 'font.json')), 'font.json missing after unpack')
        # Print basic summary
        hashes = _walk_hashes(self.unpack_dir_1)
        print(f"Unpacked {os.path.basename(self.src_bffnt)} -> {self.unpack_dir_1}")
        print(f"Files: {len(hashes)}")

    def test_02_pack_roundtrip_hash_match(self):
        # Overwrite unpacked files with edited sources from fonts/CKingMain
        source_edit_dir = os.path.join(self.fonts, 'CKingMain')
        self.assertTrue(os.path.isdir(source_edit_dir), 'Missing source edit dir fonts/CKingMain')
        # Copy only font.json and sheet_0.png (leave sheet_1.png intact per requirement)
        for name in ('font.json', 'sheet_0.png'):
            src = os.path.join(source_edit_dir, name)
            self.assertTrue(os.path.isfile(src), f'Missing {name} in source edits')
            dst = os.path.join(self.unpack_dir_1, name)
            shutil.copy2(src, dst)
        print(f"Injected edits from {source_edit_dir} into {self.unpack_dir_1}")

        # Some provided JSONs may have a minor typo in the key name for 'char'.
        # Try to parse and, if it fails, auto-fix the known pattern.
        import json
        dst_json = os.path.join(self.unpack_dir_1, 'font.json')
        try:
            with open(dst_json, 'r', encoding='utf-8') as f:
                json.load(f)
        except Exception:
            # Attempt a targeted fix and re-validate
            with open(dst_json, 'r', encoding='utf-8', errors='replace') as f:
                txt = f.read()
            fixed = txt.replace('"c:har" "', '"char": "')
            with open(dst_json, 'w', encoding='utf-8') as f:
                f.write(fixed)
            with open(dst_json, 'r', encoding='utf-8') as f:
                json.load(f)

        # Compute hashes and names before re-pack
        names_before = sorted(os.listdir(self.unpack_dir_1))
        ref_hashes = _walk_hashes(self.unpack_dir_1)
        # Pack from unpack_dir_1
        out = self.p.pack_from_json_folder(self.unpack_dir_1, self.packed_bffnt)
        self.assertTrue(os.path.isfile(out), 'Packed file not created')
        # Unpack the packed file
        out_dir = self.u.unpack_bffnt(self.packed_bffnt)
        self.assertEqual(os.path.abspath(out_dir), os.path.abspath(self.unpack_dir_2))
        # Verify file names are the same after unpack
        names_after = sorted(os.listdir(self.unpack_dir_2))
        self.assertEqual(names_before, names_after, 'Filenames differ after repack/unpack')

        # Compare per-file hashes: font.json and sheet_0.png must differ; sheet_1.png must match
        new_hashes = _walk_hashes(self.unpack_dir_2)
        print(f"Roundtrip compare: {len(ref_hashes)} files (checking specific diffs)")
        self.assertIn('font.json', ref_hashes)
        self.assertIn('sheet_0.png', ref_hashes)
        self.assertIn('sheet_1.png', ref_hashes)
        self.assertNotEqual(ref_hashes['font.json'], new_hashes.get('font.json'), 'font.json hashes should differ')
        self.assertNotEqual(ref_hashes['sheet_0.png'], new_hashes.get('sheet_0.png'), 'sheet_0.png hashes should differ')
        self.assertEqual(ref_hashes['sheet_1.png'], new_hashes.get('sheet_1.png'), 'sheet_1.png hashes should match')

    def test_03_json_contains_ukrainian_ghe_index_140(self):
        # After repack/unpack, verify glyph index 140 corresponds to 'Ґ' (U+0490)
        out_dir = self.unpack_dir_2
        font_json = os.path.join(out_dir, 'font.json')
        self.assertTrue(os.path.isfile(font_json), 'font.json missing in repacked-unpacked output')
        import json
        with open(font_json, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        glyphs = meta.get('glyphs', [])
        match = None
        for g in glyphs:
            try:
                if int(g.get('index')) == 140:
                    match = g
                    break
            except Exception:
                continue
        self.assertIsNotNone(match, 'No glyph with index 140 found')
        # Accept either explicit char or codepoint string
        cp = (match.get('codepoint') or '').upper().strip()
        ch = match.get('char') or ''
        self.assertTrue(cp == 'U+0490' or ch == 'Ґ', f"Glyph at index 140 is not Ґ/U+0490 (got cp={cp!r}, char={ch!r})")


if __name__ == '__main__':
    unittest.main(verbosity=2)
