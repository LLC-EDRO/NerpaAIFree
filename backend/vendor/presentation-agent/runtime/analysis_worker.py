"""Bounded parser process, exited before the independent render process starts."""
import json
import sys
import traceback
import contextlib
from pathlib import Path
from runtime.analysis import analyze

if __name__ == '__main__':
    try:
        with contextlib.redirect_stdout(sys.stderr):
            analyze(Path(sys.argv[1]),Path(sys.argv[2]))
        result={'ok':True}
    except Exception as error:
        reason=str(error) if isinstance(error,ValueError) and str(error).startswith('pptx_') else 'pptx_processing_failed'
        result={'error':reason,'errorType':type(error).__name__,
                'trace':[f'{Path(f.filename).name}:{f.name}:{f.lineno}' for f in traceback.extract_tb(error.__traceback__)[-6:]]}
    print(json.dumps(result))
