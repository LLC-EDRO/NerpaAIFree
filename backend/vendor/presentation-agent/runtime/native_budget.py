"""Advisory character budgets from the same physical fitter used for export.
No font, spacing or geometry mutations. Actual text is always measured again.
"""
from runtime.text_frames import TemplateTextFrame, TemplateTextFitter

def _capacity(frame, sentence, fitter):
    text=(sentence*80)[:1800]
    # The sample's length/old estimate cannot cap a large, empty native box.
    # Bracket the capacity before binary search. Starting with 900 glyphs for
    # every tiny label wastes most shaping work. The final exact search and
    # original fitter are unchanged; only the order of probes is different.
    low,high=1,min(16,len(text))
    while high<len(text) and fitter.fit(frame,[text[:high]]).status=='fits':
        low=high
        high=min(len(text),high*2)
    while low<high:
        middle=(low+high+1)//2
        if fitter.fit(frame,[text[:middle]]).status=='fits':low=middle
        else:high=middle-1
    return low,fitter.fit(frame,[text[:low]])


def measured_profile(frame, estimate, fitter):
    frame=TemplateTextFrame.model_validate(frame)
    sentence='Данные и методы. Основные результаты работы, условия и выводы. '
    wide='ИССЛЕДОВАНИЕ МЕХАНИЗМОВ 2026 ШИРОКИЕ ПОКАЗАТЕЛИ '
    if fitter.fit(frame,[sentence[:4]]).status=='unverifiable':
        sentence='Data and methods. Main findings, conditions and conclusions. '
        wide='MEASUREMENTS WORLDWIDE 2026 MAXIMUM WIDTH '
    if fitter.fit(frame,[sentence[:1]]).status!='fits':return {'maxChars':estimate}
    normal,report=_capacity(frame,sentence,fitter)
    # Wide capitals/numbers often cause repairs in dates, values and headings.
    # This is a writing target, NOT a new rejection threshold or font mutation.
    # Estimate the wide-glyph target from two real, unwrapped measurements,
    # then verify it in the ORIGINAL wrapping frame. Avoid a second exhaustive
    # binary search on every field of a large uploaded template.
    unwrapped=frame.model_copy(update={'wrap':False})
    normal_width=fitter.fit(unwrapped,[sentence]).measured_width_emu/len(sentence)
    wide_width=fitter.fit(unwrapped,[wide]).measured_width_emu/len(wide)
    wide_chars=max(1,min(normal,int(normal*normal_width/max(1,wide_width))))
    for _ in range(3):
        check=fitter.fit(frame,[(wide*80)[:wide_chars]])
        if check.status=='unverifiable':
            wide_chars=normal
            break
        if check.status=='fits':break
        wide_chars=max(1,int(wide_chars*.8))
    return {'maxChars':normal,'textFit':{
        'targetChars':max(1,int(min(normal,wide_chars)*.9)),
        'wideChars':wide_chars,
        'lines':sum(p.line_count for p in report.paragraphs),
        'wrap':frame.wrap,
        'usableWidthPt':round(report.available_width_emu/12700,2),
        'usableHeightPt':round(report.available_height_emu/12700,2),
    }}
