"""Apply explicit editor choices last, preserving native objects and relationships."""
import math
from lxml import etree
from runtime.security import NS


def parent_matrix(shape):
    a,b,c,d,e,f=1.,0.,0.,1.,0.,0.
    for parent in shape.iterancestors():
        if parent.tag!='{%s}grpSp'%NS['p']:continue
        x=parent.find('p:grpSpPr/a:xfrm',NS)
        if x is None:continue
        off,ext,child,span=(x.find('a:'+name,NS) for name in ('off','ext','chOff','chExt'))
        if any(v is None for v in (off,ext,child,span)):continue
        sx=float(ext.get('cx'))/max(1,float(span.get('cx')));sy=float(ext.get('cy'))/max(1,float(span.get('cy')))
        ox,oy=float(off.get('x')),float(off.get('y'));cx,cy=ox+float(ext.get('cx'))/2,oy+float(ext.get('cy'))/2
        angle=math.radians(float(x.get('rot',0))/60000);co,si=math.cos(angle),math.sin(angle)
        fx=-1 if x.get('flipH') in ('1','true') else 1;fy=-1 if x.get('flipV') in ('1','true') else 1
        ga,gb,gc,gd=co*fx*sx,si*fx*sx,-si*fy*sy,co*fy*sy
        tx=ox-float(child.get('x'))*sx-cx;ty=oy-float(child.get('y'))*sy-cy
        ge,gf=cx+co*fx*tx-si*fy*ty,cy+si*fx*tx+co*fy*ty
        a,b,c,d,e,f=ga*a+gc*b,gb*a+gd*b,ga*c+gc*d,gb*c+gd*d,ga*e+gc*f+ge,gb*e+gd*f+gf
    return a,b,c,d,e,f


def set_frame(shape, frame):
    props=shape.find('p:spPr',NS)
    if props is None:raise ValueError('manual_text_geometry_missing')
    x=props.find('a:xfrm',NS)
    if x is None:x=etree.Element('{%s}xfrm'%NS['a']);props.insert(0,x)
    a,b,c,d,e,f=parent_matrix(shape);det=a*d-b*c
    if abs(det)<1e-12:raise ValueError('manual_text_singular_group')
    px=(frame['x']+frame['w']/2)*12700-e;py=(frame['y']+frame['h']/2)*12700-f
    cx,cy=(d*px-c*py)/det,(-b*px+a*py)/det
    angle=math.radians(float(x.get('rot',0))/60000);co,si=math.cos(angle),math.sin(angle)
    aa,bb,cc,dd=abs(a*co+c*si),abs(b*co+d*si),abs(c*co-a*si),abs(d*co-b*si)
    divisor=aa*dd-bb*cc
    ext=x.find('a:ext',NS)
    if abs(divisor)>1e-10:
        w=(dd*frame['w']-cc*frame['h'])*12700/divisor;h=(aa*frame['h']-bb*frame['w'])*12700/divisor
    else:
        # At 45 degrees the axis-aligned frame has one degree of freedom.
        ow=float(ext.get('cx',12700)) if ext is not None else 12700
        oh=float(ext.get('cy',12700)) if ext is not None else 12700
        scale=min(frame['w']*12700/max(1,aa*ow+cc*oh),frame['h']*12700/max(1,bb*ow+dd*oh));w,h=ow*scale,oh*scale
    if min(w,h)<=0:raise ValueError('manual_text_invalid_rotated_frame')
    off=x.find('a:off',NS)
    if off is None:off=etree.Element('{%s}off'%NS['a']);x.insert(0,off)
    if ext is None:ext=etree.SubElement(x,'{%s}ext'%NS['a'])
    off.set('x',str(round(cx-w/2)));off.set('y',str(round(cy-h/2)));ext.set('cx',str(round(w)));ext.set('cy',str(round(h)))


def text_style(body,style):
    if 'size' in style:
        bp=body.find('a:bodyPr',NS)
        if bp is not None:
            for tag in ('normAutofit','spAutoFit','noAutofit'):
                for old in bp.findall('a:'+tag,NS):bp.remove(old)
            etree.SubElement(bp,'{%s}noAutofit'%NS['a'])
    for p in body.findall('a:p',NS):
        pp=p.find('a:pPr',NS)
        if pp is None:pp=etree.Element('{%s}pPr'%NS['a']);p.insert(0,pp)
        default=pp.find('a:defRPr',NS)
        if default is None:etree.SubElement(pp,'{%s}defRPr'%NS['a'])
        for run in p.findall('a:r',NS)+p.findall('a:fld',NS):
            if run.find('a:rPr',NS) is None:run.insert(0,etree.Element('{%s}rPr'%NS['a']))
    for props in body.xpath('.//a:rPr | .//a:defRPr | .//a:endParaRPr',namespaces=NS):
        if 'size' in style:props.set('sz',str(round(style['size']*100)))
        if 'bold' in style:props.set('b','1' if style['bold'] else '0')
        if 'color' in style:
            for fill in props.xpath('./a:solidFill | ./a:gradFill | ./a:noFill | ./a:pattFill | ./a:blipFill | ./a:grpFill',namespaces=NS):props.remove(fill)
            fill=etree.Element('{%s}solidFill'%NS['a']);etree.SubElement(fill,'{%s}srgbClr'%NS['a'],val=style['color'].lstrip('#').upper())
            props.insert(1 if props.find('a:ln',NS) is not None else 0,fill)


def apply_manual_text(root,layout,slide,profile=None):
    native=slide['native'];rebuilt=native.get('rebuild');styles=slide.get('textStyles',{})
    if not styles:return
    slots={s['key']:s for s in layout['slots']}
    anchors={s['key']:s for s in (profile or {}).get('textAnchors',[])}
    for path,style in styles.items():
        if not path.startswith('native.fields.'):continue
        key=path[len('native.fields.'):];spec=slots.get(key);shape=None;cell=None
        if rebuilt:
            spec=next((s for s in rebuilt.get('texts',[]) if s['key']==key),None)
            for ti,table in enumerate(rebuilt.get('tables',[])):
                for r,row in enumerate(table['rows']):
                    if key in row:
                        spec=table;cell=(r,row.index(key))
                        matches=root.xpath('.//p:graphicFrame[p:nvGraphicFramePr/p:cNvPr/@id=$id]',namespaces=NS,id=str(table.get('shapeId','')))
                        if not matches:matches=root.xpath('.//p:graphicFrame[p:nvGraphicFramePr/p:cNvPr/@name="Nerpa rebuilt table"]',namespaces=NS)[ti:ti+1]
                        shape=matches[0] if matches else None
            if shape is None and spec:
                anchor=anchors.get(key)
                matches=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(anchor['shapeId'])) if anchor else root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@name=$name]',namespaces=NS,name='Nerpa '+key)
                shape=matches[0] if matches else None
        elif spec:
            matches=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id] | .//p:graphicFrame[p:nvGraphicFramePr/p:cNvPr/@id=$id]',namespaces=NS,id=str(spec['shapeId']))
            shape=matches[0] if matches else None;cell=spec.get('cell')
        if shape is None:raise ValueError('manual_text_target_missing')
        target=shape
        if cell is not None:target=shape.findall('.//a:tbl/a:tr',NS)[cell[0]].findall('a:tc',NS)[cell[1]]
        body=target.find('p:txBody',NS) if cell is None else target.find('a:txBody',NS)
        if body is None:raise ValueError('manual_text_body_missing')
        if any(k in style for k in ('x','y','w','h')):
            if cell is not None:raise ValueError('manual_text_cell_geometry')
            frame={**spec,**native.get('geometryRepairs',{}).get(key,{}),**style};set_frame(shape,frame)
        text_style(body,style)
