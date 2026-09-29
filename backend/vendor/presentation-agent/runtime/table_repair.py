"""Give regular table rows room for one line without enlarging the table.

PowerPoint can store row minima shorter than a single glyph plus cell margins.
Redistribute spare row height in the working copy; never flatten cells, drop
rows, change columns or reduce fonts. Horizontal merges retain their spans;
vertical merges require coupled row constraints and are left unchanged.
"""
import math
from lxml import etree
from runtime.security import NS, xml
from runtime.tables import cell_frame
from runtime.text_frames import TemplateTextFitter


def recover_table_rows(parts, model):
    def flatten(items):
        for item in items:
            yield item
            yield from flatten(item.children)
    shapes = {s.source_object_id:s for slide in model.slides for s in flatten(slide.objects)}
    roots, changes = {}, []
    fitter = TemplateTextFitter()
    for table in model.tables:
        shape = shapes.get(table.linked_object_id)
        if not shape or shape.hidden or table.source_level != 'slide' or not table.rows:
            continue
        cols = len(table.grid_column_widths_emu)
        cells = [cell for row in table.rows for cell in row.cells]
        if not cols or any(c.v_merge or c.row_span != 1 for c in cells):
            continue
        # Verify exact column coverage, including source horizontal merges and
        # omitted continuation cells. No column/merge metadata is rewritten.
        coverage = [[col for cell in row.cells if not cell.h_merge
                     for col in range(cell.col, cell.col + cell.col_span)] for row in table.rows]
        if any(sorted(columns) != list(range(cols)) for columns in coverage):
            continue
        heights = [row.height_emu or 0 for row in table.rows]
        minimum = []
        for row in table.rows:
            required = 0
            for cell in row.cells:
                if cell.h_merge: continue
                frame = cell_frame(table, cell, shape)
                measured = fitter.fit(frame.model_copy(update={'height_emu':10**9, 'wrap':False}), ['АруЙgj'])
                if measured.status == 'unverifiable' or not measured.paragraphs:
                    required = -1
                    break
                required = max(required, math.ceil(frame.top_emu + frame.bottom_emu + measured.measured_height_emu + 6350))
            minimum.append(required)
        if min(heights) <= 0 or min(minimum) <= 0 or sum(minimum) > sum(heights) or all(h >= m for h,m in zip(heights,minimum)):
            continue
        spare = [max(0,h-m) for h,m in zip(heights,minimum)]
        available = sum(heights)-sum(minimum)
        adjusted = [m + math.floor(available*s/sum(spare)) for m,s in zip(minimum,spare)]
        adjusted[max(range(len(spare)),key=lambda i:spare[i])] += sum(heights)-sum(adjusted)
        root = roots.setdefault(shape.source_part, xml(parts[shape.source_part]))
        tables = root.xpath('.//p:graphicFrame[p:nvGraphicFramePr/p:cNvPr[@id=$id]]//a:tbl',namespaces=NS,id=str(shape.shape_id))
        if len(tables) != 1: continue
        rows = tables[0].findall('a:tr',NS)
        if len(rows) != len(adjusted): continue
        for row, height in zip(rows,adjusted): row.set('h',str(height))
        changes.append(dict(part=shape.source_part,shapeId=shape.shape_id,beforeHeightsEmu=heights,afterHeightsEmu=adjusted))
    result = dict(parts)
    for part in {c['part'] for c in changes}:
        result[part] = etree.tostring(roots[part],xml_declaration=True,encoding='UTF-8',standalone=True)
    return result,changes
