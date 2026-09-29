"""Patch delivered native objects and render only changed pages. No generation/QA."""
from pathlib import Path
import math
import zipfile
import pymupdf
from runtime.security import read_package,xml,NS,slide_order
from runtime.fill import replace_text,serialize
from runtime.manual_text import set_frame,text_style,parent_matrix
from runtime.package_cleanup import compact_package
from runtime.notes_compat import normalize_notes_masters
from runtime.render import render


def write_package(parts,path):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in parts.items():archive.writestr(name,data)


def body_value(target):
    body=target.find('p:txBody',NS)
    if body is None:body=target.find('a:txBody',NS)
    return '\n'.join(''.join(p.xpath('.//a:t/text()',namespaces=NS)) for p in body.findall('a:p',NS)) if body is not None else None


def normalized(value):return ''.join((value or '').split())


def frame_of(shape):
    x=shape.find('p:spPr/a:xfrm',NS)
    if x is None:x=shape.find('p:xfrm',NS)
    if x is None:return None
    off,ext=x.find('a:off',NS),x.find('a:ext',NS)
    if off is None or ext is None:return None
    a,b,c,d,e,f=parent_matrix(shape)
    ox,oy,w,h=float(off.get('x')),float(off.get('y')),float(ext.get('cx')),float(ext.get('cy'))
    angle=math.radians(float(x.get('rot',0))/60000);co,si=math.cos(angle),math.sin(angle)
    width=abs(a*co+c*si)*w+abs(c*co-a*si)*h;height=abs(b*co+d*si)*w+abs(d*co-b*si)*h
    cx=a*(ox+w/2)+c*(oy+h/2)+e;cy=b*(ox+w/2)+d*(oy+h/2)+f
    return dict(x=(cx-width/2)/12700,y=(cy-height/2)/12700,w=width/12700,h=height/12700)


def locate_target(root,field):
    binding=field.get('binding') or {};identity=binding.get('shapeId')
    if identity is not None:
        shapes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id] | .//p:graphicFrame[p:nvGraphicFramePr/p:cNvPr/@id=$id]',namespaces=NS,id=str(identity))
        if len(shapes)==1:
            shape=shapes[0];target=shape
            if binding.get('cell') is not None:
                r,c=binding['cell'];rows=shape.findall('.//a:tbl/a:tr',NS)
                target=rows[r].findall('a:tc',NS)[c] if r<len(rows) and c<len(rows[r].findall('a:tc',NS)) else None
            if target is not None and normalized(body_value(target))==normalized(field['before']):return shape,target
    # Rebuilt decks may assign new native IDs. Resolve by original text AND geometry,
    # never by template name, ordinal, or a first equal label.
    candidates=[];expected=field['frame']
    for shape in root.xpath('.//p:sp | .//p:graphicFrame',namespaces=NS):
        frame=frame_of(shape)
        if frame is None:continue
        cells=shape.findall('.//a:tbl/a:tr',NS)
        targets=[]
        if cells:
            heights=[float(r.get('h',1)) for r in cells];widths=[float(c.get('w',1)) for c in shape.findall('.//a:tbl/a:tblGrid/a:gridCol',NS)]
            for r,row in enumerate(cells):
                for c,cell in enumerate(row.findall('a:tc',NS)):
                    if c>=len(widths):continue
                    targets.append((cell,dict(x=frame['x']+frame['w']*sum(widths[:c])/sum(widths),y=frame['y']+frame['h']*sum(heights[:r])/sum(heights),w=frame['w']*widths[c]/sum(widths),h=frame['h']*heights[r]/sum(heights))))
        else:targets=[(shape,frame)]
        for target,box in targets:
            if body_value(target) is not None and normalized(body_value(target))==normalized(field['before']):
                score=sum(abs(box[k]-expected[k]) for k in ('x','y','w','h'))
                candidates.append((score,shape,target))
    candidates.sort(key=lambda c:c[0])
    if candidates and (len(candidates)==1 or candidates[1][0]-candidates[0][0]>.1):return candidates[0][1:]
    raise ValueError('pptx_edit_target_ambiguous')


def select_pages(parts,indexes):
    result=dict(parts);pres=xml(parts['ppt/presentation.xml']);listing=pres.find('p:sldIdLst',NS)
    nodes=list(listing);selected={nodes[i].get('{%s}id'%NS['r']) for i in indexes}
    for child in nodes:listing.remove(child)
    for i in indexes:listing.append(nodes[i])
    for child in pres.findall('p:custShowLst',NS):pres.remove(child)
    rels=xml(parts['ppt/_rels/presentation.xml.rels'])
    for rel in list(rels):
        if rel.get('Type','').endswith('/slide') and rel.get('Id') not in selected:rels.remove(rel)
    result['ppt/presentation.xml']=serialize(pres);result['ppt/_rels/presentation.xml.rels']=serialize(rels)
    return compact_package(result)[0]


def editor_patch(folder,request):
    output=Path(request['output']);output.mkdir(parents=True,exist_ok=True)
    parts=read_package((Path(folder)/'source.pptx').read_bytes(),generated=True);order=slide_order(parts)
    patches=request['patches'];indexes=[p['index'] for p in patches]
    if not indexes or len(set(indexes))!=len(indexes) or any(type(i)!=int or i<0 or i>=len(order) for i in indexes):raise ValueError('pptx_edit_indexes_invalid')
    for patch in patches:
        root=xml(parts[order[patch['index']]])
        # Resolve before modifying so equal labels remain distinguishable.
        targets=[locate_target(root,f) for f in patch['fields']]
        for field,(shape,target) in zip(patch['fields'],targets):
            if 'value' in field:replace_text(target,field['value'])
            style=field.get('style') or {}
            if any(k in style for k in ('x','y','w','h')):
                if target is not shape:raise ValueError('pptx_edit_cell_geometry')
                set_frame(shape,{**field['frame'],**style})
            body=target.find('p:txBody',NS)
            if body is None:body=target.find('a:txBody',NS)
            text_style(body,style)
        parts[order[patch['index']]]=serialize(root)
    # Repair legacy notes metadata as well as future exports, without running
    # whole-deck generation or modifying unrelated native slide objects.
    parts,_=normalize_notes_masters(parts)
    write_package(parts,output/'presentation.pptx')
    selected=output/'selected';selected.mkdir()
    write_package(select_pages(parts,indexes),selected/'changed.pptx')
    rendered=render(selected/'changed.pptx',selected,request['soffice'])
    if rendered!=len(indexes):raise ValueError('pptx_edit_render_count')
    with pymupdf.open(Path(folder)/'previews/presentation.pdf') as before,pymupdf.open(selected/'presentation.pdf') as changed,pymupdf.open() as result:
        if len(before)!=len(order):raise ValueError('pptx_edit_pdf_count')
        for index in range(len(order)):
            source=changed if index in indexes else before;page=indexes.index(index) if index in indexes else index
            result.insert_pdf(source,from_page=page,to_page=page,links=False)
        # Page-at-a-time insertion drops internal links. Restore destinations in
        # the full deck's numbering, and retain external links on rendered pages.
        for index in range(len(order)):
            source=changed if index in indexes else before;page=indexes.index(index) if index in indexes else index
            links=[link for link in source[page].get_links() if link['kind']!=pymupdf.LINK_GOTO]
            links += [link for link in before[index].get_links() if link['kind']==pymupdf.LINK_GOTO]
            for link in links:
                link={k:v for k,v in link.items() if k not in ('xref','id')}
                result[index].insert_link(link)
        result.set_toc(before.get_toc());result.set_metadata(before.metadata);result.save(output/'presentation.pdf',garbage=3,deflate=True)
    for i,index in enumerate(indexes):(output/f'slide-{index}.png').write_bytes((selected/f'slide-{i}.png').read_bytes())
    return dict(pptxWritten=True,pdfAvailable=True,changedSlides=indexes,renderedSlides=len(indexes),issues=[],warnings=[])
