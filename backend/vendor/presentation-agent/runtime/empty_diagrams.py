"""Detect outlined empty nodes connected as a diagram, not decorative icon art."""
from runtime.security import NS


def empty_diagram_nodes(root):
    tree=root.find('p:cSld/p:spTree',NS)
    if tree is None:return []
    endpoints=[]
    for line in tree.findall('p:cxnSp',NS):
        t=line.find('p:spPr/a:xfrm',NS)
        if t is None or t.get('rot','0')!='0':continue
        off,ext=t.find('a:off',NS),t.find('a:ext',NS)
        if off is None or ext is None:continue
        x,y,w,h=[int(v)/12700 for v in (off.get('x'),off.get('y'),ext.get('cx'),ext.get('cy'))]
        fx,fy=t.get('flipH') in ('1','true'),t.get('flipV') in ('1','true')
        endpoints.extend([(x+(w if fx else 0),y+(h if fy else 0)),(x+(0 if fx else w),y+(0 if fy else h))])
    nodes=[]
    for sp in tree.findall('p:sp',NS):
        if sp.find('p:txBody',NS) is None or ''.join(sp.xpath('.//a:t/text()',namespaces=NS)).strip():continue
        geom=sp.find('p:spPr/a:prstGeom',NS);t=sp.find('p:spPr/a:xfrm',NS)
        if geom is None or geom.get('prst') not in ('rect','roundRect') or t is None or t.get('rot','0')!='0':continue
        if sp.find('p:spPr/a:noFill',NS) is None or sp.find('p:spPr/a:ln',NS) is None or sp.find('p:spPr/a:ln/a:noFill',NS) is not None:continue
        off,ext=t.find('a:off',NS),t.find('a:ext',NS)
        if off is None or ext is None:continue
        x,y,w,h=[int(v)/12700 for v in (off.get('x'),off.get('y'),ext.get('cx'),ext.get('cy'))]
        if w<25 or h<12:continue
        if any(x-2<=px<=x+w+2 and y-2<=py<=y+h+2 and min(abs(px-x),abs(px-x-w),abs(py-y),abs(py-y-h))<=2 for px,py in endpoints):
            nodes.append(int(sp.find('p:nvSpPr/p:cNvPr',NS).get('id')))
    return nodes if len(nodes)>=3 else []
