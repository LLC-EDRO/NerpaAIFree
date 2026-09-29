"""Repair downloadable generated decks without changing content or AI checkpoints.

Run with Python + lxml. Originals and internal source.pptx checkpoints are kept.
All affected files are backed up under data/validation before replacement.
"""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import zipfile

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / 'vendor/presentation-agent'))
from runtime.package_cleanup import normalize_presentation_catalogs, VERSION


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    projects = BACKEND / 'data/projects'
    backup = BACKEND / 'data/validation/catalog-repair-v2'
    files = []
    for project in sorted(projects.iterdir()):
        if not project.is_dir():
            continue
        state = json.loads((project / 'project.json').read_text())
        if state.get('busy'):
            raise RuntimeError(f'Project is busy: {project.name}')
        files.extend(project.glob('output-r*/presentation.pptx'))
        files.extend(project.glob('assembled-r*/presentation.pptx'))
    changes = []
    for path in sorted(files):
        original = path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            assert archive.testzip() is None
            infos = archive.infolist()
            parts = {item.filename: archive.read(item) for item in infos}
            assert len(parts) == len(infos)
            fixed, removed = normalize_presentation_catalogs(parts)
            if not removed:
                continue
            assert [name for name in parts if parts[name] != fixed[name]] == ['ppt/presentation.xml']
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as output:
                output.comment = archive.comment
                for item in infos:
                    output.writestr(item, fixed[item.filename])
        payload = buffer.getvalue()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert archive.testzip() is None
            assert {name: archive.read(name) for name in archive.namelist()} == fixed
        replacements = {path: payload}
        for name in ('render-quality.json', 'package-cleanup.json'):
            receipt_path = path.parent / name
            if not receipt_path.exists():
                continue
            receipt = json.loads(receipt_path.read_text())
            if 'pptxSha256' in receipt:
                assert receipt['pptxSha256'] == sha(original), f'Stale receipt: {receipt_path}'
                receipt['pptxSha256'] = sha(payload)
            if name == 'package-cleanup.json':
                receipt.update(version=VERSION, removedEmptyCatalogs=removed)
            replacements[receipt_path] = (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode()
        for target in replacements:
            saved = backup / target.relative_to(projects)
            saved.parent.mkdir(parents=True, exist_ok=True)
            if saved.exists():
                raise RuntimeError(f'Backup already exists: {saved}')
            shutil.copy2(target, saved)
        for target, data in replacements.items():
            temporary = target.with_name(target.name + '.catalog-repair.tmp')
            temporary.write_bytes(data)
            temporary.replace(target)
        changes.append(dict(file=str(path.relative_to(projects)), before=sha(original), after=sha(payload),
                            removedEmptyCatalogs=removed, changedParts=['ppt/presentation.xml']))
    if changes:
        (backup / 'report.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(checked=len(files), repaired=len(changes), report=str(backup / 'report.json'))))


if __name__ == '__main__':
    main()
