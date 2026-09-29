"""Container-only bridge. Outputs a bounded framed stream, never host paths.

All input mounts are read-only. Mutable parsing/conversion state lives on a
size-limited tmpfs; no output bind mount or Docker socket is available here.
"""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import traceback
import re

sys.path.insert(0, '/opt/pptx')

INPUT_FILES = ('source.pptx', 'prepared.pptx', 'analysis.json', 'frames.json',
               'parser.json', 'effective-parser.json', 'analyzer.json')


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (160 * 1024**2, 160 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    request = json.loads(sys.stdin.buffer.readline(8 * 1024**2 + 1))
    action = request['action']
    work = Path('/work')
    key=request.get('inputKey')
    if key is not None and not re.fullmatch(r'[a-f0-9-]{36}',key):
        raise ValueError('pptx_sandbox_request')
    input_root=Path('/input')/key/'input' if key else Path('/input')
    if key:
        # Sequential calls share fonts and the converter, never mutable slide
        # files. Old output cannot satisfy a later request's artifact contract.
        for name in ('template','output'):
            shutil.rmtree(work/name,ignore_errors=True)
    for name in ('home', 'tmp', 'template', 'output'):
        (work/name).mkdir(exist_ok=True)
    os.umask(0o077)
    for name in (*INPUT_FILES, 'previews/presentation.pdf'):
        source = input_root/'template'/name
        if source.is_file():
            (work/'template'/name).parent.mkdir(exist_ok=True)
            shutil.copyfile(source, work/'template'/name)
    request['folder'] = str(work/'template')
    request['output'] = str(work/'output')
    request['soffice'] = '/usr/bin/soffice'
    request['images'] = {name: str(input_root/'images'/name) for name in request.get('images', {})}
    from bridge import main as run, PptxPreflightError
    try:
        # Keep library prints out of the host protocol and bound user diagnostics.
        original_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(request))
        with contextlib.redirect_stdout(sys.stderr):
            result = run()
        sys.stdin = original_stdin
    except Exception as error:
        reason = str(error) if isinstance(error, ValueError) and str(error).startswith('pptx_') else 'pptx_processing_failed'
        # Locations/types only: never log document text, exception messages,
        # host paths, XML, credentials or model context.
        result = dict(error=reason, errorType=type(error).__name__,
                      trace=[f'{Path(frame.filename).name}:{frame.name}:{frame.lineno}'
                             for frame in traceback.extract_tb(error.__traceback__)[-6:]],
                      **(dict(issues=error.issues[:80]) if isinstance(error, PptxPreflightError) else {}))
    emit(dict(result=result))
    paths = []
    if not result.get('error'):
        if action == 'font_cache':
            paths = [(work/'output'/'font-index.json','font-index.json')]
            paths += [(p,'fontconfig/'+p.name) for p in sorted((work/'output'/'fontconfig').glob('*.cache-*'))]
        elif action == 'analyze':
            paths = [(work/'template'/name, name) for name in INPUT_FILES if name != 'source.pptx']
            paths += [(path, 'previews/'+path.name) for path in sorted((work/'template'/'previews').glob('*'))]
        elif action in ('export', 'assemble', 'editor_patch'):
            paths = [(path, path.name) for path in sorted((work/'output').glob('*')) if path.is_file()]
        elif action == 'preview':
            # Failed render previews are diagnostic inputs for AI repair too.
            paths = [(work/'output'/'slide-0.png', 'slide-0.png')]
        elif action == 'editor_background':
            paths = [(path,path.name) for path in sorted((work/'output').glob('*')) if path.name=='slide-0.png' or re.fullmatch(r'[a-f0-9]{64}\.ttf',path.name)]
        elif action == 'thumbnails':
            paths = [(path,path.name) for path in sorted((work/'output').glob('slide-*.png'))]
        elif action == 'repair_context':
            paths = [(path,path.name) for path in sorted((work/'output').glob('slide-*.png'))]
    for path, name in paths:
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError('pptx_sandbox_output_invalid')
        emit(dict(file=name, size=path.stat().st_size))
        with path.open('rb') as stream:
            while chunk := stream.read(48 * 1024):
                emit(dict(chunk=base64.b64encode(chunk).decode('ascii')))
        emit(dict(endFile=True))
    emit(dict(done=True))


if __name__ == '__main__':
    main()
