"""Clipping bounds in the same MuPDF paint order as get_bboxlog().

Image bounds in bboxlog/image_info describe the uncropped image. Only a known
rectangular clip proves an opaque cover; arbitrary paths/masks do not.
"""
import pymupdf
from runtime.security import xml


def rectangular_clip(node):
    matrix = pymupdf.Matrix(*map(float, node.get('transform', '1 0 0 1 0 0').split()))
    points = []
    for part in node:
        if part.tag in ('moveto', 'lineto'):
            if part.tag == 'moveto' and points:
                return None
            points.append(pymupdf.Point(float(part.get('x')), float(part.get('y'))) * matrix)
        elif part.tag != 'closepath':
            return None
    if len(points) < 4:
        return None
    bounds = pymupdf.Rect(min(p.x for p in points), min(p.y for p in points),
                         max(p.x for p in points), max(p.y for p in points))
    edges = list(zip(points, points[1:] + points[:1]))
    if any(abs(a.x-b.x) > .01 and abs(a.y-b.y) > .01 for a,b in edges):
        return None
    area = abs(sum(a.x*b.y-b.x*a.y for a,b in edges)) / 2
    return bounds if abs(area-bounds.get_area()) < .1 else None


def paint_clips(page, paint):
    mupdf = pymupdf.mupdf
    buffer = mupdf.FzBuffer(1024)
    output = mupdf.FzOutput(buffer)
    device = mupdf.fz_new_trace_device(output)
    try:
        mupdf.fz_run_page(page.this, device, mupdf.FzMatrix(), mupdf.FzCookie())
    finally:
        mupdf.fz_close_device(device)
        output.fz_close_output()
    root = xml(b'<trace>' + pymupdf.JM_BinFromBuffer(buffer) + b'</trace>')
    result, kinds = {}, []
    names = {'fill_path': 'fill-path', 'stroke_path': 'stroke-path',
             'fill_text': 'fill-text', 'stroke_text': 'stroke-text', 'ignore_text': 'ignore-text',
             'fill_image': 'fill-image', 'fill_image_mask': 'fill-image', 'fill_shade': 'fill-shade'}

    def walk(parent, inherited):
        clips = [inherited]
        for node in parent:
            tag = node.tag
            if tag.startswith('clip_'):
                bound = rectangular_clip(node) if tag == 'clip_path' else None
                clips.append(clips[-1] & bound if clips[-1] is not None and bound is not None else None)
            elif tag == 'pop_clip':
                if len(clips) < 2:
                    raise ValueError('pptx_clip_analysis_failed')
                clips.pop()
            elif tag in ('group', 'layer', 'tile', 'mask'):
                safe = tag in ('group', 'layer') and float(node.get('alpha', '1')) >= .99 and node.get('blendmode', 'Normal') == 'Normal'
                walk(node, clips[-1] if safe else None)
                if tag == 'mask':
                    clips.append(None)
            elif tag in names:
                result[len(kinds)] = clips[-1] if float(node.get('alpha', '1')) >= .99 else None
                kinds.append(names[tag])
    walk(root, page.rect)
    # Never silently assign a crop to the wrong image / paint operation.
    if kinds != [kind for kind, _ in paint]:
        raise ValueError('pptx_clip_analysis_failed')
    return result
