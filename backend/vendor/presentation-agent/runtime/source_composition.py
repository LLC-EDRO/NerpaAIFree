"""Source-proven text/rail contacts, not a blanket overlap exemption."""
import re
from runtime.geometry import inside, intersection


def add_source_contacts(profile, slots, order):
    anchors=profile['textAnchors']
    for anchor in anchors:
        contacts=[]
        for obj in profile['pictures']+profile['decorations']:
            if obj['shapeId'] in profile.get('keepShapeIds',[]):continue
            b=obj['box']
            # Thin rules can be raster pictures as well as native lines. Only
            # existing contacts BEHIND this text qualify; no photos or charts.
            if (order.get(obj['shapeId'],10**9)>=order.get(anchor['shapeId'],-1)
                or intersection(anchor,b)<=.5):continue
            axis='horizontal' if b['w']>=b['h'] else 'vertical'
            short,long=(b['h'],b['w']) if axis=='horizontal' else (b['w'],b['h'])
            if 0<=short<=min(4,max(1,anchor['size']*.12)) and long>=max(24,short*20):
                contacts.append(dict(shapeId=obj['shapeId'],axis=axis,box=b))
        if contacts:anchor['sourceLineContacts']=contacts
    # Repeated numeric anchors identify a metric row even when a parser calls
    # every large number a title. Neither slide IDs nor template names matter.
    source={s['key']:s for s in slots}
    candidates=[a for a in anchors if a.get('allowFrameOverflow') and not a.get('rotation')
        and a.get('verticalAlign')=='bottom'
        and any(c['axis']=='horizontal' for c in a.get('sourceLineContacts',[]))
        and re.fullmatch(r'[\s\d.,%+−–—\-/×x]+',source.get(a['sourceKey'],{}).get('text',''))]
    groups=[];used=set()
    for anchor in candidates:
        if anchor['key'] in used:continue
        peers=[a for a in candidates if a['key'] not in used
            and all(a.get(k)==anchor.get(k) for k in ('font','bold','color','align','verticalAlign'))
            and abs(a['size']-anchor['size'])<.2 and abs(a['y']-anchor['y'])<.5
            and abs(a['h']-anchor['h'])<.5]
        peers.sort(key=lambda a:a['x'])
        if len(peers)<2 or any(a['x']+a['w']>b['x']+.5 for a,b in zip(peers,peers[1:])):continue
        groups.append(dict(keys=[a['key'] for a in peers],axis='bottom',sameSize=True))
        used.update(a['key'] for a in peers)
    if groups:profile['metricAlignmentGroups']=groups


def source_line_contact(profile, text, other):
    anchor=next((a for a in profile.get('textAnchors',[]) if a['key']==text.get('key')),None)
    if not anchor:return False
    for contact in anchor.get('sourceLineContacts',[]):
        if contact['shapeId']!=other.get('shapeId'):continue
        b=contact['box']
        if any(abs(other[k]-b[k])>.01 for k in ('x','y','w','h')):continue
        if not inside(text,anchor.get('container') or anchor.get('repairRegion') or profile['page']):continue
        # Retain the same thin band; horizontal text growth need not preserve
        # an exact overlap area (which used to treat a 6pt move as a collision).
        cross,size=('y','h') if contact['axis']=='horizontal' else ('x','w')
        before=max(0,min(anchor[cross]+anchor[size],b[cross]+b[size])-max(anchor[cross],b[cross]))
        after=max(0,min(text[cross]+text[size],b[cross]+b[size])-max(text[cross],b[cross]))
        if after<=before+.5:return True
    return False
