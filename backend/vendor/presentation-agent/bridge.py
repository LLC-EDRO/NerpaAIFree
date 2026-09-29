"""One bounded invocation, JSON stdin/stdout. No provider calls or inherited app config."""
import json
import sys
import hashlib
import subprocess
from pathlib import Path
from runtime.analysis import validate_fields
from runtime.fill import fill
from runtime.render import render
from runtime.fonts import effective_source,requested_font_names,font_environment_fingerprint
from runtime.layout_metadata import layout_metadata
from runtime.preflight import preflight, PptxPreflightError
from runtime.render_quality import inspect_render

def main():
    request = json.load(sys.stdin)
    folder = Path(request['folder']).resolve()
    source = folder/'source.pptx'
    action = request['action']
    if action == 'editor_patch':
        from runtime.editor_patch import editor_patch
        return editor_patch(folder,request)
    if action == 'editor_background':
        from runtime.editor_background import editor_background
        return editor_background(folder,request)
    if action == 'font_cache':
        from runtime.font_cache import build_font_cache
        return build_font_cache(Path(request['output']),request['fontDirectories'])
    if action == 'font_requirements':
        return dict(families=requested_font_names(source))
    if action == 'font_environment':
        return dict(fingerprint=font_environment_fingerprint(requested_font_names(source)))
    if action == 'layout_metadata':
        return layout_metadata(folder,include_budgets=True)
    if action == 'thumbnails':
        # Intermediate visual feedback uses the real native slide and renderer.
        # Full-deck QA still runs at export; previews cannot certify the result.
        output = Path(request['output']).resolve()
        output.mkdir(parents=True, exist_ok=True)
        images = {key: Path(path).read_bytes() for key,path in request['images'].items()}
        filled = fill(source, folder, request['slides'], images, output/'presentation.pptx', False, True)
        if not filled.get('pptxWritten'): raise ValueError('pptx_preview_failed')
        count = render(output/'presentation.pptx', output, request['soffice'])
        return dict(slides=count)
    if action == 'rebuild_context':
        from runtime.rebuild import profiles
        return profiles(folder)
    if action == 'repair_context':
        from runtime.repair_context import repair_context
        return repair_context(folder,Path(request['output']),request['soffice'])
    if action == 'preflight':
        return preflight(folder, request['layoutIds'], request['soffice'])
    if action == 'assemble':
        from runtime.assemble import assemble
        output = Path(request['output']).resolve()
        output.mkdir(parents=True, exist_ok=True)
        result=assemble(source, folder, request['layoutIds'], output/'presentation.pptx')
        from runtime.assembly_cache import select_cached_analysis
        result['analysis']=select_cached_analysis(folder,request['layoutIds'],output)
        return result
    if action == 'analyze':
        # Release every parser allocation before LibreOffice starts. Python's
        # allocator can retain hundreds of MB even after model objects die.
        worker = subprocess.run([sys.executable, '-m', 'runtime.analysis_worker', str(source), str(folder)],
                                capture_output=True, text=True, timeout=180, check=False)
        if worker.returncode:
            raise ValueError('pptx_resource_limit' if worker.returncode == -9 else 'pptx_analysis_failed')
        result=json.loads(worker.stdout)
        if result.get('error'):return result
        value=json.loads((folder/'analysis.json').read_text())
        count = render(effective_source(source,folder,value), folder/'previews', request['soffice'])
        if count != len(value['layouts']): raise ValueError('pptx_preview_count_mismatch')
        return value
    if action == 'fit':
        from runtime.safe_text_expansion import expansion_report
        expansions={};options={}
        try:
            issues=validate_fields(folder, request['slides'],expansions,options if request.get('repairOptions') else None,request.get('repairOptions'))
            return dict(issues=issues,fieldChanges=expansion_report(expansions),geometryOptions=options)
        except ValueError:
            if len(request['slides'])<2 or request.get('repairOptions'):raise
            # Isolate a malformed slide inside this sandbox: valid siblings
            # retain their checks instead of buying unrelated AI repairs.
            issues=[];changes=[];errors=[]
            for index,slide in enumerate(request['slides']):
                try:
                    local={}
                    issues.extend(dict(i,slide=index) for i in validate_fields(folder,[slide],local))
                    changes.extend(dict(c,slide=index+1) for c in expansion_report(local))
                except ValueError as error:
                    code=str(error) if str(error).startswith('pptx_') else 'pptx_processing_failed'
                    errors.append(dict(slide=index,code=code))
            return dict(issues=issues,fieldChanges=changes,geometryOptions={},slideErrors=errors)
    if action in ('export', 'preview'):
        if action == 'preview' and len(request['slides']) != 1:
            raise ValueError('pptx_preview_count_mismatch')
        output = Path(request['output']).resolve()
        output.mkdir(parents=True, exist_ok=True)
        images = {key: Path(path).read_bytes() for key,path in request['images'].items()}
        allow_warnings = action == 'export' and request.get('allowQualityWarnings') is True
        result = fill(source, folder, request['slides'], images, output/'presentation.pptx', request.get('withNotes',False), allow_warnings)
        if result.get('pptxWritten'):
            result['pdfAvailable'] = False
            try:
                render(output/'presentation.pptx', output, request['soffice'])
                result['pdfAvailable'] = True
            except Exception:
                if not allow_warnings: raise
                result.setdefault('warnings',[]).append(dict(reason='preview_unavailable'))
            quality = dict(version=0,pages=[],issues=[])
            if result['pdfAvailable']:
                rendered_slides=request['slides']
                try:
                    quality = inspect_render(folder, rendered_slides, output/'presentation.pdf')
                except Exception:
                    if not allow_warnings: raise
                    result.setdefault('warnings',[]).append(dict(reason='quality_check_unavailable'))
                if quality['issues']:
                    import tempfile,shutil
                    from runtime.rendered_fit import repair_rendered_fit
                    try:
                        with tempfile.TemporaryDirectory(prefix='fit-',dir=output.parent) as tmp:
                            candidate=Path(tmp)
                            shutil.copy2(output/'presentation.pptx',candidate/'presentation.pptx')
                            proposed,changes=repair_rendered_fit(folder,rendered_slides,candidate/'presentation.pptx',quality)
                            if changes:
                                render(candidate/'presentation.pptx',candidate,request['soffice'])
                                checked=inspect_render(folder,proposed,candidate/'presentation.pdf')
                                signature=lambda i:(i.get('slide'),i.get('key'),i.get('reason'),tuple(i.get('details',[])))
                                if set(map(signature,checked['issues']))<set(map(signature,quality['issues'])):
                                    for file in candidate.iterdir():
                                        if file.is_file():shutil.copy2(file,output/file.name)
                                    quality=checked;rendered_slides=proposed
                                    result.setdefault('fontChanges',[]).extend(changes)
                    except Exception:
                        result.setdefault('warnings',[]).append(dict(reason='measured_fit_unavailable'))
                if any(w.get('reason')=='rendered_text_low_contrast' for p in quality['pages'] for w in p.get('warnings',[])):
                    # A single local colour pass, not another LLM/render repair loop.
                    # Commit PPTX/PDF/previews together only after measured improvement.
                    import tempfile,shutil
                    from runtime.rendered_contrast import repair_rendered_contrast
                    try:
                        with tempfile.TemporaryDirectory(prefix='contrast-',dir=output.parent) as tmp:
                            candidate=Path(tmp)
                            shutil.copy2(output/'presentation.pptx',candidate/'presentation.pptx')
                            changes=repair_rendered_contrast(folder,rendered_slides,candidate/'presentation.pptx',output/'presentation.pdf',quality)
                            if changes:
                                render(candidate/'presentation.pptx',candidate,request['soffice'])
                                checked=inspect_render(folder,rendered_slides,candidate/'presentation.pdf')
                                signature=lambda i:(i.get('slide'),i.get('key'),i.get('reason'),tuple(i.get('details',[])))
                                score=lambda q:sum(w.get('affectedRatio',1) for p in q['pages'] for w in p.get('warnings',[]) if w.get('reason')=='rendered_text_low_contrast')
                                if set(map(signature,checked['issues']))<=set(map(signature,quality['issues'])) and score(checked)<score(quality):
                                    for file in candidate.iterdir():
                                        if file.is_file():shutil.copy2(file,output/file.name)
                                    quality=checked
                                    result.setdefault('colorChanges',[]).extend(changes)
                    except Exception:
                        # The original, already rendered files remain deliverable.
                        result.setdefault('warnings',[]).append(dict(reason='contrast_repair_unavailable'))
            if action == 'preview':
                return dict(issues=quality['issues'],warnings=[w for page in quality['pages'] for w in page.get('warnings',[])])
            if result['pdfAvailable']:
                from runtime.rendered_frame_overlap import resolve_editing_frame_overlaps
                result['issues'],resolved=resolve_editing_frame_overlaps(folder,rendered_slides,output/'presentation.pptx',result['issues'],quality)
                if resolved:quality['verifiedFrameOverlaps']=resolved
            (output/'package-cleanup.json').write_text(json.dumps(dict(**result['packageCleanup'], pptxSha256=hashlib.sha256((output/'presentation.pptx').read_bytes()).hexdigest())))
            quality['issues'] = result['issues'] + quality['issues']
            (output/'render-quality.json').write_text(json.dumps(quality, ensure_ascii=False))
            (output/'field-changes.json').write_text(json.dumps(dict(version=1,pptxSha256=hashlib.sha256((output/'presentation.pptx').read_bytes()).hexdigest(),changes=result.get('fieldChanges',[]),colorChanges=result.get('colorChanges',[]),fontChanges=result.get('fontChanges',[])),ensure_ascii=False))
            result['issues'] = quality['issues']
            result['renderQuality'] = dict(version=quality['version'], pages=quality['pages'])
        return result
    raise ValueError('pptx_unknown_operation')

if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception as error:
        # Sanitized diagnostics only, not XML, user text, paths, or environment.
        reason = str(error) if isinstance(error, ValueError) and str(error).startswith('pptx_') else type(error).__name__
        print(json.dumps(dict(error=reason, **(dict(issues=error.issues[:80]) if isinstance(error, PptxPreflightError) else {}))))
        sys.exit(1)
