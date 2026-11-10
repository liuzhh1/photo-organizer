"""Independent Windows service entry point with persistent diagnostics."""
from __future__ import annotations

import argparse
import faulthandler
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--log-prefix', type=Path, required=True)
    args = parser.parse_args()
    args.log_prefix.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = open(str(args.log_prefix) + '.log', 'a', encoding='utf-8', buffering=1)
    sys.stderr = open(str(args.log_prefix) + '.error.log', 'a', encoding='utf-8', buffering=1)
    faulthandler.enable(file=sys.stderr, all_threads=True)
    environment = Path(sys.executable).parent
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      TOKENIZERS_PARALLELISM='false', PYTHONUTF8='1',
                      CONDA_PREFIX=str(environment))
    os.environ['PATH'] = os.pathsep.join([str(environment), str(environment / 'Library' / 'bin'),
                                        str(environment / 'Scripts'), os.environ.get('PATH', '')])
    if os.name == 'nt':
        dll_directories = []
        for folder in (environment / 'Library' / 'bin', environment / 'Lib' / 'site-packages' / 'torch' / 'lib'):
            if folder.is_dir():
                dll_directories.append(os.add_dll_directory(str(folder)))
    print(f'{datetime.now().isoformat()} startup pid={os.getpid()} python={sys.executable}', flush=True)
    sys.argv = [str(Path(__file__).with_name('app.py')), '--no-browser', '--port', str(args.port)]
    try:
        from app import main as serve
        result = serve()
        print(f'{datetime.now().isoformat()} server exited: {result}', flush=True)
        return result
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        raise


if __name__ == '__main__':
    raise SystemExit(main())
