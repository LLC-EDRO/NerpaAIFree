"""Upgrade supported cached frame measurements without rewriting user uploads."""
import json
from app.presentation.models import ShapeElementModel
from runtime.fonts import effective_source
from runtime.security import read_package
from runtime.shape_text_geometry import ShapeTextGeometry
from runtime.text_frames import source_text_frame, visible_frame_width


def read_frames(folder, model=None, parts=None, data=None):
    frames = json.loads((folder/'frames.json').read_text())
    data = data or json.loads((folder/'analysis.json').read_text())
    def page_bounds():
        for layout in data['layouts']:
            for slot in layout['slots']:
                frame = frames.get(layout['id'], {}).get(slot['key'])
                if frame and frame.get('width_emu', 0) > 0:
                    frame['visible_width_emu'] = visible_frame_width(frame, slot, data['width'])
    pending = {(slide, key) for slide, values in frames.items() for key, frame in values.items()
               if 'nonrectangular_text_frame_unsupported' in frame.get('reason_codes', [])}
    if not pending:
        page_bounds()
        return frames
    if model is None:
        path = folder/'effective-parser.json'
        from runtime.model_store import read_model
        model = read_model(path if path.exists() else folder/'parser.json')
    if parts is None:
        parts = read_package(effective_source(folder/'source.pptx', folder, data).read_bytes())
    geometry = ShapeTextGeometry(parts)
    def flatten(items):
        for item in items:
            yield item
            yield from flatten(item.get('children', []))
    for slide in model['slides']:
        for item in flatten(slide['objects']):
            key = f"s{item.get('shape_id')}"
            if (slide['slide_id'], key) in pending:
                frames[slide['slide_id']][key] = source_text_frame(ShapeElementModel.model_validate(item), geometry).model_dump()
    page_bounds()
    return frames
