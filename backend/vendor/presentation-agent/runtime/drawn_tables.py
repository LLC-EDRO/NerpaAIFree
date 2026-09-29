"""Recover unequivocal ruled text grids as editable PowerPoint tables.

Detection uses repeated row/column geometry AND a full-width rule after every
row. Cards, charts, incomplete grids and rotated artwork are left untouched.
Only the working copy changes; surrounding backgrounds and branding survive.
"""
from copy import deepcopy
from statistics import median
from io import BytesIO
import posixpath
from PIL import Image
from lxml import etree
from runtime.security import NS, xml


def node(parent, tag, **attrs):
    prefix, name = tag.split(':')
    return etree.SubElement(parent, '{%s}%s' % (NS[prefix], name), **{k: str(v) for k,v in attrs.items()})


def local_box(shape):
    x = shape.find('p:spPr/a:xfrm', NS)
    if x is None:return None
    try:
        if int(x.get('rot','0')) or x.get('flipH') in ('1','true') or x.get('flipV') in ('1','true'):return None
    except ValueError:return None
    off, ext = x.find('a:off', NS), x.find('a:ext', NS)
    if off is None or ext is None: return None
    try: return dict(x=int(off.get('x'))/12700, y=int(off.get('y'))/12700,
                     w=int(ext.get('cx'))/12700, h=int(ext.get('cy'))/12700)
    except (TypeError, ValueError): return None


def text_value(shape):
    return ''.join(t.text or '' for t in shape.findall('p:txBody//a:t', NS)).strip()


def picture_color(shape, parts, relationships):
    blip=shape.find('p:blipFill/a:blip',NS)
    path=relationships.get(blip.get('{%s}embed'%NS['r'])) if blip is not None else None
    if path not in parts:return None
    try:
        with Image.open(BytesIO(parts[path])) as image:
            return image.convert('RGBA').resize((1,1)).getpixel((0,0))
    except (OSError,ValueError,Image.DecompressionBombError):return None


def preserve_rule_colors(parent, rows, rules, region, parts, relationships):
    # Raster rules are common in exported templates. Resolve their actual ink
    # against the original backdrop: PDF renderers can ignore table-border alpha.
    background=(255,255,255)
    first=min(parent.index(s['element']) for row in rows for s in row)
    for shape in list(parent)[:first]:
        b=local_box(shape)
        if not b or not (b['x']<=region['x'] and b['y']<=region['y'] and b['x']+b['w']>=region['x']+region['w'] and b['y']+b['h']>=region['y']+region['h']):continue
        color=picture_color(shape,parts,relationships)
        solid=shape.find('p:spPr/a:solidFill/a:srgbClr',NS)
        if solid is not None:
            try:color=(*bytes.fromhex(solid.get('val')),255)
            except ValueError:pass
        if color and len(color)==4:background=tuple(round(v*color[3]/255+old*(1-color[3]/255)) for v,old in zip(color,background))
    for rule in rules:
        color=picture_color(rule['element'],parts,relationships)
        if color:rule['color']=''.join(f'{round(v*color[3]/255+bg*(1-color[3]/255)):02X}' for v,bg in zip(color,background))


def candidates(parent):
    texts, rules = [], []
    for shape in parent:
        b = local_box(shape)
        if not b or b['w'] <= 0: continue
        value = text_value(shape)
        if value and shape.tag == '{%s}sp' % NS['p']:
            # Filled cards, placeholders and rich artwork are not table cells.
            if (shape.find('p:spPr/a:noFill', NS) is None or
                shape.find('p:spPr/a:ln/a:noFill', NS) is None or
                shape.find('p:nvSpPr/p:nvPr/p:ph', NS) is not None): continue
            body=shape.find('p:txBody/a:bodyPr',NS)
            if body is not None and (body.get('vert') not in (None,'horz') or int(body.get('rot','0'))):continue
            texts.append(dict(b, element=shape))
        elif not value and b['h'] <= 2 and b['w'] >= 40 and b['w']/max(b['h'], .1) >= 25:
            if shape.tag in ('{%s}pic'%NS['p'], '{%s}sp'%NS['p'], '{%s}cxnSp'%NS['p']):
                # A no-fill helper with an invisible stroke is not a rule.
                if shape.tag != '{%s}pic'%NS['p'] and shape.find('p:spPr/a:noFill',NS) is not None and shape.find('p:spPr/a:ln/a:noFill',NS) is not None: continue
                rules.append(dict(b, element=shape))
    return texts, rules


def find_grids(parent):
    texts, rules = candidates(parent)
    bands = []
    for rule in sorted(rules, key=lambda s:(s['x'], s['w'], s['y'])):
        band = next((b for b in bands if abs(b[0]['x']-rule['x']) <= 2 and abs(b[0]['w']-rule['w']) <= 2), None)
        if band is None: bands.append([rule])
        else: band.append(rule)
    used = set()
    for band in bands:
        band.sort(key=lambda r:r['y'])
        # A table needs a header and at least two independent records to be
        # inferred from artwork. Native two-row tables need no such inference.
        if len(band) < 3: continue
        gaps = [b['y']-a['y'] for a,b in zip(band,band[1:])]
        pitch = median(gaps)
        if pitch < 8 or any(abs(g-pitch) > max(2,pitch*.12) for g in gaps): continue
        rows = []
        for i,rule in enumerate(band):
            top = band[i-1]['y'] if i else rule['y']-pitch
            row = sorted((s for s in texts if s['element'] not in used and
                          s['x'] >= rule['x']-2 and s['x'] < rule['x']+rule['w']-2 and
                          s['y'] >= top-.5 and s['y']+s['h'] <= rule['y']+1),key=lambda s:s['x'])
            if not 2 <= len(row) <= 8: break
            if rows and (len(row)!=len(rows[0]) or any(abs(a['x']-b['x'])>2 for a,b in zip(row,rows[0]))): break
            if any(b['x']-a['x'] < 20 for a,b in zip(row,row[1:])): break
            rows.append(row)
        if len(rows)!=len(band): continue
        region = dict(x=band[0]['x'],y=band[0]['y']-pitch,w=band[0]['w'],h=band[-1]['y']-band[0]['y']+pitch)
        cell_elements = {s['element'] for row in rows for s in row}
        # Reject overlapping unrelated text rather than deleting it.
        if any(s['element'] not in cell_elements and region['x']<=s['x']<region['x']+region['w'] and region['y']<=s['y']<region['y']+region['h'] for s in texts): continue
        removed=cell_elements | {r['element'] for r in band}
        conflict=False
        for shape in parent:
            b=local_box(shape)
            if b is None or shape in removed:continue
            if shape.find('p:spPr/a:noFill',NS) is not None and shape.find('p:spPr/a:ln/a:noFill',NS) is not None and shape.tag!='{%s}pic'%NS['p'] and not text_value(shape):continue
            contains=b['x']<=region['x'] and b['y']<=region['y'] and b['x']+b['w']>=region['x']+region['w'] and b['y']+b['h']>=region['y']+region['h']
            if not contains and min(b['x']+b['w'],region['x']+region['w'])>max(b['x'],region['x'])+1 and min(b['y']+b['h'],region['y']+region['h'])>max(b['y'],region['y'])+1:
                conflict=True;break
        if conflict:continue
        used.update(cell_elements)
        yield rows, band, region


def add_native_table(parent, rows, rules, region, identity):
    frame = etree.Element('{%s}graphicFrame'%NS['p'])
    nv = node(frame,'p:nvGraphicFramePr')
    node(nv,'p:cNvPr',id=identity,name='Editable table from source grid')
    node(nv,'p:cNvGraphicFramePr'); node(nv,'p:nvPr')
    transform = node(frame,'p:xfrm')
    node(transform,'a:off',x=round(region['x']*12700),y=round(region['y']*12700))
    node(transform,'a:ext',cx=round(region['w']*12700),cy=round(region['h']*12700))
    data=node(node(frame,'a:graphic'),'a:graphicData',uri='http://schemas.openxmlformats.org/drawingml/2006/table')
    table=node(data,'a:tbl');node(table,'a:tblPr',firstRow='0',bandRow='0')
    grid=node(table,'a:tblGrid')
    edges=[region['x']]+[s['x'] for s in rows[0][1:]]+[region['x']+region['w']]
    for left,right in zip(edges,edges[1:]): node(grid,'a:gridCol',w=round((right-left)*12700))
    top=region['y']
    for row,rule in zip(rows,rules):
        tr=node(table,'a:tr',h=round((rule['y']-top)*12700));top=rule['y']
        for c,source in enumerate(row):
            tc=node(tr,'a:tc')
            body=deepcopy(source['element'].find('p:txBody',NS));body.tag='{%s}txBody'%NS['a'];tc.append(body)
            bp=body.find('a:bodyPr',NS)
            if bp is not None:
                for k in ('lIns','rIns','tIns','bIns','anchor','anchorCtr'):bp.attrib.pop(k,None)
            props=node(tc,'a:tcPr',marL=round(max(0,source['x']-edges[c])*12700),marR=12700,marT=12700,marB=12700,anchor='ctr')
            for side in ('L','R','T'): node(node(props,'a:ln'+side),'a:noFill')
            line=node(props,'a:lnB',w=round(max(.25,rule['h'])*12700))
            original=rule['element'].find('p:spPr/a:ln',NS)
            fill=original.find('a:solidFill',NS) if original is not None else None
            if rule.get('color'):node(node(line,'a:solidFill'),'a:srgbClr',val=rule['color'])
            elif fill is not None: line.append(deepcopy(fill))
            else:
                color=node(node(line,'a:solidFill'),'a:schemeClr',val='tx1');node(color,'a:alpha',val=10000)
            node(props,'a:noFill')
    removed=[s['element'] for row in rows for s in row]+[r['element'] for r in rules]
    position=min(parent.index(s) for s in removed)
    for shape in removed:parent.remove(shape)
    parent.insert(position,frame)


def recover_drawn_tables(parts):
    result=dict(parts);changes=[]
    for part,content in parts.items():
        if not part.startswith('ppt/slides/') or not part.endswith('.xml') or '/_rels/' in part:continue
        root=xml(content);tree=root.find('p:cSld/p:spTree',NS)
        if tree is None:continue
        relpart=posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')
        relationships={r.get('Id'):posixpath.normpath(posixpath.join(posixpath.dirname(part),r.get('Target',''))) for r in xml(parts[relpart]) if r.get('TargetMode')!='External'} if relpart in parts else {}
        identity=max((int(n.get('id','0')) for n in root.findall('.//p:cNvPr',NS)),default=0)+1
        before=len(changes)
        for parent in [tree,*tree.findall('.//p:grpSp',NS)]:
            for rows,rules,region in list(find_grids(parent)):
                ids={n.get('id') for row in rows for s in row for n in s['element'].findall('p:nvSpPr/p:cNvPr',NS)}
                if any(n.get('spid') in ids for n in root.xpath('.//*[@spid]')):continue
                preserve_rule_colors(parent,rows,rules,region,parts,relationships)
                add_native_table(parent,rows,rules,region,identity)
                changes.append(dict(part=part,shapeId=identity,rows=len(rows),columns=len(rows[0]),source='ruled_text_grid'))
                identity+=1
        if len(changes)>before:result[part]=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    return result,changes
