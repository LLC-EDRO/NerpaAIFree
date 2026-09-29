"""Native table cells: preserve the grid, merges, borders and paragraph styles."""
from runtime.text_frames import TemplateTextFrame as NativeTextFrame, TemplateParagraph as NativeParagraph
from runtime.text_frames import apply_bullet_properties
from copy import deepcopy
from runtime.security import NS, xml
import math

MAX_TABLE_ROWS = 10

def column_count(columns, key, original):
    value = (columns or {}).get(key, original)
    if type(value) is not int or not 2 <= value <= original:
        raise ValueError('pptx_invalid_table_columns')
    return value

def resize_table_columns(table, count):
    """Reduce unneeded properties, preserving total width and retained styles."""
    grid = table.find('a:tblGrid', NS)
    cols = grid.findall('a:gridCol', NS)
    if type(count) is not int or not 2 <= count <= len(cols):raise ValueError('pptx_invalid_table_columns')
    if count == len(cols):return
    rows=table.findall('a:tr',NS)
    if any(len(r.findall('a:tc',NS))!=len(cols) or any(c.get(a) not in ((None,'1') if a in ('gridSpan','rowSpan') else (None,'0','false')) for c in r.findall('a:tc',NS) for a in ('gridSpan','rowSpan','hMerge','vMerge')) for r in rows):
        raise ValueError('pptx_table_grid_not_regular')
    total=sum(int(c.get('w')) for c in cols);selected=sum(int(c.get('w')) for c in cols[:count])
    if min(total,selected)<=0:raise ValueError('pptx_invalid_table_columns')
    widths=[round(int(c.get('w'))*total/selected) for c in cols[:count]];widths[-1]+=total-sum(widths)
    for col,width in zip(cols,widths):col.set('w',str(width))
    for col in cols[count:]:grid.remove(col)
    for row in rows:
        for cell in row.findall('a:tc',NS)[count:]:row.remove(cell)

def table_weights(weights,key,count):
    values=(weights or {}).get(key,[1]*count)
    if not isinstance(values,list) or len(values)!=count or any(type(w) not in (int,float) or not math.isfinite(w) or not 1<=w<=10000 for w in values):
        raise ValueError('pptx_invalid_table_weights')
    return values

def apply_native_text_sizes(layout,frames,sizes):
    if not sizes:return frames
    frames=deepcopy(frames)
    if not isinstance(sizes,dict) or len(sizes)>120:raise ValueError('pptx_invalid_text_sizes')
    for key,size in sizes.items():
        slot=next((s for s in layout['slots'] if s['key']==key),None)
        if not slot or isinstance(size,bool) or not isinstance(size,(int,float)) or not max(12,slot['size']*.75)<=size<=slot['size']:
            raise ValueError('pptx_invalid_text_sizes')
        for p in frames[key]['paragraphs']:p['font_size_pt']=size
    return frames

# Compatibility for the table writer; the same bounded measurements now support
# a small source-native text edit without rewriting its content.
apply_table_text_sizes=apply_native_text_sizes


def expanded_table_layout(layout, frames, counts, weights=None, columns=None):
    """A regular source grid may change rows and lose unneeded columns in-place.
    Old persisted slides without counts retain their original exact topology.
    """
    if not counts:
        return layout, frames
    if not isinstance(counts, dict) or len(counts) > 12:
        raise ValueError('pptx_invalid_table_rows')
    if weights is not None and (not isinstance(weights,dict) or any(k not in counts for k in weights)):
        raise ValueError('pptx_invalid_table_weights')
    if columns is not None and (not isinstance(columns,dict) or any(k not in counts for k in columns)):
        raise ValueError('pptx_invalid_table_columns')
    result, measurements = deepcopy(layout), deepcopy(frames)
    for key, count in counts.items():
        if not isinstance(key, str) or not key.isdecimal() or type(count) is not int or not 1 <= count < MAX_TABLE_ROWS:
            raise ValueError('pptx_invalid_table_rows')
        slots = [s for s in layout['slots'] if s.get('shapeId') == int(key) and 'cell' in s]
        if not slots:
            raise ValueError('pptx_table_identity_mismatch')
        rows = max(s['cell'][0] for s in slots) + 1
        cols = max(s['cell'][1] for s in slots) + 1
        cells = {tuple(s['cell']): s for s in slots}
        if rows < 2 or not 2 <= cols <= 8 or len(cells) != rows * cols:
            raise ValueError('pptx_table_grid_not_regular')
        for r in range(rows):
            for c in range(cols):
                s = cells.get((r,c))
                if not s or s['key'] != f's{key}_r{r}_c{c}' or any(abs(s[p]-cells[(0,c)][p]) > .1 for p in ('x','w')) or any(abs(s[p]-cells[(r,0)][p]) > .1 for p in ('y','h')):
                    raise ValueError('pptx_table_grid_not_regular')
        top = cells[(1,0)]['y']
        height = max(s['y']+s['h'] for s in slots)-top
        target_cols=column_count(columns,key,cols)
        left=cells[(0,0)]['x'];width=max(s['x']+s['w'] for s in slots)-left
        selected=[cells[(0,c)]['w'] for c in range(target_cols)]
        widths=[w*width/sum(selected) for w in selected]
        row_weights=table_weights(weights,key,count)
        heights=[height*w/sum(row_weights) for w in row_weights]
        result['slots'] = [s for s in result['slots'] if s['shapeId'] != int(key) or 'cell' not in s]
        for r in range(count+1):
            exemplar = 0 if r==0 else rows-1 if r == count else 1+(r-1) % (rows-1)
            for c in range(target_cols):
                source = cells[(exemplar,c)]
                s = dict(source, key=f's{key}_r{r}_c{c}', cell=[r,c], x=left+sum(widths[:c]),w=widths[c],
                         **(dict(y=top+sum(heights[:r-1]),h=heights[r-1]) if r else {}))
                result['slots'].append(s)
                if source['key'] in frames:
                    frame = deepcopy(frames[source['key']])
                    frame['height_emu'] = round(frame['height_emu'] * s['h']/source['h'])
                    if 'width_emu' in frame:frame['width_emu']=round(frame['width_emu']*s['w']/source['w'])
                    measurements[s['key']] = frame
    return result, measurements


def resize_table_rows(root, counts, weights=None, columns=None):
    """Clone styled body rows in the output only. No changes to the uploaded file,
    surrounding artwork, theme IDs, cell borders or font sizes. Reducing columns
    redistributes their widths inside the same table rectangle.
    """
    for key, count in (counts or {}).items():
        if type(count) is not int or not 1 <= count < MAX_TABLE_ROWS:raise ValueError('pptx_invalid_table_rows')
        identities = root.xpath('.//p:cNvPr[@id=$id]', namespaces=NS, id=key)
        if len(identities) != 1:
            raise ValueError('pptx_table_identity_mismatch')
        tables = identities[0].getparent().getparent().xpath('.//a:tbl', namespaces=NS)
        if len(tables) != 1:
            raise ValueError('pptx_table_identity_mismatch')
        table = tables[0]
        rows = table.findall('a:tr', NS)
        cols = len(table.findall('a:tblGrid/a:gridCol', NS))
        if len(rows) < 2 or any(len(r.findall('a:tc', NS)) != cols for r in rows):
            raise ValueError('pptx_table_grid_not_regular')
        if any(c.get(a) not in ((None,'1') if a in ('gridSpan','rowSpan') else (None,'0','false')) for r in rows for c in r.findall('a:tc',NS) for a in ('gridSpan','rowSpan','hMerge','vMerge')):
            raise ValueError('pptx_table_grid_not_regular')
        height = sum(int(r.get('h')) for r in rows[1:])
        if height <= 0:
            raise ValueError('pptx_table_invalid_height')
        row_weights=table_weights(weights,key,count)
        heights=[int(height*w/sum(row_weights)) for w in row_weights]
        heights[-1]+=height-sum(heights)
        insertion = list(table).index(rows[1])
        for row in rows[1:]:
            table.remove(row)
        for r in range(count):
            source = rows[-1] if r == count-1 else rows[1+r % (len(rows)-1)]
            row = deepcopy(source)
            row.set('h', str(heights[r]))
            # Row creation IDs are optional; cloned values must not be duplicated.
            for node in row.xpath('.//*[local-name()="rowId" or local-name()="creationId"]'):
                node.getparent().remove(node)
            table.insert(insertion+r,row)
        resize_table_columns(table,column_count(columns,key,cols))

def cell_frame(table, cell, item):
    style = cell.effective_style
    frame = NativeTextFrame(source_element_id=f'{item.source_object_id}:{cell.row}:{cell.col}', source_fingerprint=str((item.raw_xml_ref or {}).get('sha256') or ''),
        width_emu=sum(w or 0 for w in table.grid_column_widths_emu[cell.col:cell.col+cell.col_span]),
        height_emu=sum(r.height_emu or 0 for r in table.rows[cell.row:cell.row+cell.row_span]))
    for key in ('left_emu','right_emu','top_emu','bottom_emu'):
        if cell.margins.get(key) is not None: setattr(frame,key,cell.margins[key])
    full = item.geometry_full
    # Table grid sizes are local coordinates and transform with the entire group.
    for p in cell.rich_text.paragraphs if cell.rich_text else []:
        run = next((r for r in p.runs if r.text.strip()),None)
        font = run.effective_style if run else p.default_style
        prop=p.properties
        def spacing(name, default):
            value=getattr(prop,name)
            return (getattr(prop,name+'_unit') or 'percent',value) if value is not None else default
        paragraph=NativeParagraph(has_text=bool(''.join(r.text for r in p.runs).strip()),
            font_family=(font.font_family if font else None) or style.font_family,
            font_size_pt=(font.font_size_pt if font else None) or style.font_size_pt or 18,
            bold=bool((font.bold if font else False) or style.font_weight==700),italic=bool((font.italic if font else False) or style.italic),
            margin_left_emu=prop.margin_left_emu or 0,margin_right_emu=prop.margin_right_emu or 0,indent_emu=prop.indent_emu or 0,
            line_spacing=spacing('line_spacing',('percent',100)),space_before=spacing('space_before',('points',0)),space_after=spacing('space_after',('points',0)))
        if p.bullet and p.bullet.type not in (None,'none'):
            # Only admit lists whose actual paragraph XML we can measure.
            if not prop.raw_xml or not apply_bullet_properties(paragraph, xml(prop.raw_xml.encode())) or not paragraph.bullet_text:
                paragraph.reason_codes.append('table_bullet_unsupported')
        frame.paragraphs.append(paragraph)
    if not frame.paragraphs: frame.reason_codes.append('table_font_unresolved')
    return frame

def table_slots(table,item,box):
    for row in table.rows:
        for physical_index,cell in enumerate(row.cells):
            if cell.h_merge or cell.v_merge: continue
            frame=cell_frame(table,cell,item)
            style=cell.effective_style
            paragraph=frame.paragraphs[0] if frame.paragraphs else None
            size=paragraph.font_size_pt if paragraph and paragraph.font_size_pt else 20
            original=cell.text
            # OOXML may omit merge-continuation cells. Geometry uses logical columns,
            # but replacement must address the physical <a:tc> in the source row.
            yield dict(key=f's{item.shape_id}_r{cell.row}_c{cell.col}',shapeId=item.shape_id,cell=[cell.row,physical_index],
                role='table_header' if cell.row==0 else 'table_cell',text=original[:1800],maxChars=max(8,min(300,int(frame.width_emu/12700/max(1,size*.62))*max(1,int(frame.height_emu/12700/(size*1.35))))),
                x=box['x']+sum(w or 0 for w in table.grid_column_widths_emu[:cell.col])/12700,y=box['y']+sum(r.height_emu or 0 for r in table.rows[:cell.row])/12700,
                w=frame.width_emu/12700,h=frame.height_emu/12700,font=(paragraph.font_family if paragraph else None) or 'Arial',size=size,
                bold=bool(paragraph and paragraph.bold),color=style.text_color or '#202020',align='left',
                defaultFontSize=18 if getattr(cell,'rich_text',None) and any(not ((next((r.effective_style.font_size_pt for r in p.runs if r.text.strip()),None)) or (p.default_style and p.default_style.font_size_pt) or style.font_size_pt) for p in cell.rich_text.paragraphs) else None),frame
