"""Apply advisory AI alignment without moving objects or touching table/brand text."""
from lxml import etree
from runtime.security import NS

def apply_alignment(root,layout,native):
    for key,choice in {**native.get('textAlignment',{}),**(native.get('alignmentIntent') or {})}.items():
        slot=next((s for s in layout['slots'] if s['key']==key),None)
        value=native.get('fields',{}).get(key,'').strip()
        if not slot or 'cell' in slot or slot.get('role') in ('logo','brand','decoration','footer','page_number') or len(value)>160 or len(value.split('\n'))>3:continue
        if not isinstance(choice,dict) or len(choice.get('reason',''))<5:continue
        nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]/p:txBody',namespaces=NS,id=str(slot['shapeId']))
        if len(nodes)!=1:continue
        body=nodes[0];props=body.find('a:bodyPr',NS)
        if props is None:props=etree.Element('{%s}bodyPr'%NS['a']);body.insert(0,props)
        # Vertical/rotated writing has its own axes; leave it untouched.
        if props.get('vert','horz')!='horz' or props.get('rot','0')!='0':continue
        if choice.get('vertical')=='center' and slot['h']>=slot['size']*2.2:props.set('anchor','ctr')
        if choice.get('horizontal')=='center':
            for paragraph in body.findall('a:p',NS):
                pp=paragraph.find('a:pPr',NS)
                if pp is None:pp=etree.Element('{%s}pPr'%NS['a']);paragraph.insert(0,pp)
                pp.set('algn','ctr')
