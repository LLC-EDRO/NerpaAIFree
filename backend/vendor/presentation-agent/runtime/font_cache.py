"""Build caches from installed fonts only, before any uploaded document is read.

The host mounts this snapshot read-only. Each document still has its own
disposable sandbox and converter profile; no uploaded bytes enter this cache.
"""
import json
import subprocess
from pathlib import Path
from lxml import etree


def build_font_cache(output,directories):
    from runtime.fonts import TemplateFontResolver, font_metadata
    output.mkdir(parents=True, exist_ok=True)
    records = {}
    index = TemplateFontResolver._build_index()
    for paths in index.values():
        for path in paths:
            if str(path) not in records:
                records[str(path)] = font_metadata(path)
    (output/'font-index.json').write_text(json.dumps(dict(records=records,index={k:[str(p) for p in v] for k,v in index.items()}), default=lambda v: sorted(v)))
    cache = output/'fontconfig'
    cache.mkdir()
    root = etree.Element('fontconfig')
    etree.SubElement(root, 'cachedir').text = str(cache)
    etree.SubElement(root, 'include', ignore_missing='yes').text = '/etc/fonts/fonts.conf'
    for folder in TemplateFontResolver.FONT_ROOTS:
        if folder.is_dir():
            etree.SubElement(root, 'dir').text = str(folder)
    config = output/'fonts.conf'
    config.write_bytes(etree.tostring(root))
    import os
    roots=[path for path in directories if Path(path).is_dir()]
    result=subprocess.run(['fc-cache', '--force', *roots], env={**os.environ, 'FONTCONFIG_FILE': str(config)},
                          timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    # Only mounted directories need preparation; image-owned font caches were
    # built with the image. Fontconfig still validates all directory timestamps.
    count=len(list(cache.glob('*.cache-*')))
    if result.returncode or not count:
        raise ValueError('pptx_font_cache_unavailable')
    config.unlink()
    return dict(fonts=len(records),cachedDirectories=count)
