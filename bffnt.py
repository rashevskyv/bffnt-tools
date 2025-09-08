#!/usr/bin/env python3
"""
Легкий CLI-обгортка над модулями пакера/анпакера.

Використання:
  Розпакування:
    python3 bffnt.py [-R|--rotate180] [-Y|--flipY] [-r|--recursive] [-a|--all] [-v|--verbose] <шлях_до_*.bffnt|тека>
  Пакування:
    python3 bffnt.py pack|p [-v|--verbose] <тека_з_font.json> [вихід.bffnt]
"""

import os
import sys
from typing import List

from bffnt_unpack import unpack_bffnt  # re-exported API


def _collect_bffnts(base: str, recursive: bool) -> List[str]:
    exts = ('.bffnt', '.bcfnt', '.brfnt')
    files: List[str] = []
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
    rotate180 = False
    flip_y = False
    verbose = False
    scan_all = False
    recursive = False
    args = sys.argv[1:]
    flags = {'--rotate180', '-R', '--flipY', '-Y', '--all', '-a', '--r', '-r', '--recursive', '-v', '--verbose'}
    paths: List[str] = []

    if args and args[0].lower() in ('pack', 'p'):
        from bffnt_pack import pack_from_json_folder
        # parse optional -v for pack
        pack_args = args[1:]
        pv = False
        fa = []
        for a in pack_args:
            if a in ('-v', '--verbose'):
                pv = True
            else:
                fa.append(a)
        folder = fa[0] if len(fa) >= 1 else os.path.join(os.path.dirname(__file__), 'CKingMain')
        out_path = fa[1] if len(fa) >= 2 else None
        pack_from_json_folder(folder, out_path, verbose=pv)
        return

    i = 0
    while i < len(args):
        a = args[i]
        if a in ('--rotate180', '-R'):
            rotate180 = True
        elif a in ('--flipY', '-Y'):
            flip_y = True
        elif a in ('--all', '-a'):
            scan_all = True
        elif a in ('--r', '-r', '--recursive'):
            recursive = True
        elif a in ('-v', '--verbose'):
            verbose = True
        elif a.startswith('-') and a not in flags:
            print('Невідомий прапорець:', a, file=sys.stderr)
            return sys.exit(2)
        else:
            paths.append(a)
        i += 1

    targets: List[str] = []
    if paths:
        for p in paths:
            targets.extend(_collect_bffnts(p, recursive))
    else:
        if scan_all:
            targets = _collect_bffnts(os.getcwd(), recursive)
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            targets = _collect_bffnts(here, recursive=False)

    if not targets:
        print('Не знайдено файлів *.bffnt/*.bcfnt/*.brfnt для розпакування')
        return sys.exit(0)

    ok = 0
    fail = 0
    for src in targets:
        try:
            out_dir = unpack_bffnt(src, rotate180=rotate180, flip_y=flip_y, verbose=verbose)
            print(f'OK: {os.path.basename(src)} → {out_dir}')
            ok += 1
        except Exception as ex:
            print(f'ПОМИЛКА у {os.path.basename(src)}: {ex}', file=sys.stderr)
            fail += 1
    print(f'Готово: успішно {ok}, помилок {fail}')


if __name__ == '__main__':
    main()
