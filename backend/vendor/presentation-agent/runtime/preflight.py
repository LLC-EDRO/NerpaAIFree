"""One readiness rule for upload, cached layout discovery and checkout.

Probe overflow is advisory: it depends on the replacement text and paragraph
styles actually used. Only unsupported source structure/fonts block discovery.
Every generated payload must still pass the physical fit and occlusion checks.
Never shrink source typography, change geometry or suppress security checks.
"""
import hashlib
import json
import shutil
from runtime.fonts import effective_source
from runtime.security import read_package
from runtime.text_frames import TemplateTextFitter, TemplateTextFrame
from runtime.frame_store import read_frames

OVERFLOW_WARNING = 'В исходном текстовом поле недостаточно места при его размере шрифта. Для автоматического заполнения используется другой макет этого PPTX.'
UNVERIFIABLE_WARNING = 'Не удалось проверить исходное оформление текстового поля. Макет сохранён для просмотра.'
FORMATTING_WARNING = 'В текстовом поле есть неподдерживаемые параметры оформления. Макет сохранён для просмотра.'


def layout_frame_issues(layout, frames, fitter):
    issues = []
    for slot in layout['slots']:
        key = slot['key']
        if key not in frames:
            raise ValueError('pptx_frame_metadata_missing')
        frame = TemplateTextFrame.model_validate(frames[key])
        if not frame.paragraphs:
            issues.append(dict(sourceSlideId=layout['id'], key=key, paragraph=0,
                               reason='unverifiable', details=['source_text_frame_missing']))
        for index, report in enumerate(fitter.probe(frame, 'I')):
            if report.status != 'fits':
                issues.append(dict(sourceSlideId=layout['id'], key=key, paragraph=index,
                                   reason=report.status, details=report.reason_codes))
    return issues


def layout_readiness(layout, frames, fitter):
    issues = layout_frame_issues(layout, frames, fitter)
    warnings = [w for w in layout.get('warnings', []) if w not in (OVERFLOW_WARNING, UNVERIFIABLE_WARNING)]
    old_issues = layout.get('preflightIssues', [])
    recovered_geometry = bool(old_issues) and all(
        issue.get('details') == ['nonrectangular_text_frame_unsupported'] for issue in old_issues
    ) and not any(issue['reason'] != 'overflow' for issue in issues)
    if recovered_geometry:
        warnings = [w for w in warnings if w != FORMATTING_WARNING]
    # Recover old cached uploads only when our former probe was the sole gate.
    # Unknown explicit disables, SmartArt and parser failures remain disabled.
    source_usable = layout.get('structurallyUsable', bool(layout.get('usable')) or (
        bool(layout.get('preflightIssues')) and not warnings))
    if recovered_geometry and not warnings:
        source_usable = True
    blocking = [issue for issue in issues if issue['reason'] != 'overflow']
    if blocking:
        warnings.append(UNVERIFIABLE_WARNING)
    return dict(usable=bool(source_usable) and bool(layout['slots']) and not blocking,
                structurallyUsable=bool(source_usable), warnings=list(dict.fromkeys(warnings)),
                preflightIssues=blocking, contentFitIssues=[issue for issue in issues if issue['reason'] == 'overflow'])


class PptxPreflightError(ValueError):
    def __init__(self, issues):
        font = any('font' in reason for issue in issues for reason in issue['details'])
        super().__init__('pptx_preflight_font_unavailable' if font else 'pptx_preflight_geometry')
        self.issues = issues


def preflight(folder, layout_ids, soffice):
    source = folder/'source.pptx'
    data = json.loads((folder/'analysis.json').read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest() != data['sha256']:
        raise ValueError('pptx_source_changed')
    read_package(source.read_bytes())
    parts = read_package(effective_source(source, folder, data).read_bytes())
    if not shutil.which(soffice):
        raise ValueError('pptx_renderer_unavailable')
    frames = read_frames(folder, parts=parts, data=data)
    layouts = {layout['id']: layout for layout in data['layouts']}
    fitter = TemplateTextFitter()
    issues = []
    for slide_id in dict.fromkeys(layout_ids):
        layout = layouts.get(slide_id)
        if not layout or not layout['slots']:
            raise ValueError('pptx_layout_unavailable')
        if layout.get('requiresRebuild'):
            continue  # Output must use the separately validated rebuilt scene.
        readiness = layout_readiness(layout, frames.get(slide_id, {}), fitter)
        issues.extend(readiness['preflightIssues'])
        if not readiness['usable'] and not readiness['preflightIssues']:
            raise ValueError('pptx_layout_unavailable')
    if issues:
        raise PptxPreflightError(issues)
    return dict(ready=True)
