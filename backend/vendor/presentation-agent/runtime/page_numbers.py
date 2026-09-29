"""Update native pagination text in-place, keeping runs, styles and placement."""
import re
from runtime.security import NS

PAGINATION = re.compile(r'^\s*(?:(?:слайд|стр\.?|страница|slide|page|p\.?|№)\s*)?(?:[—–-]\s*)?(?P<current>\d{1,3})(?:(?P<separator>\s*(?:/|из|of)\s*)(?P<total>\d{1,3}))?(?:\s*[—–-])?\s*$', re.I)

def _position(shape):
    off=shape.find('p:spPr/a:xfrm/a:off',NS)
    if off is None:return None
    x,y=float(off.get('x',0)),float(off.get('y',0))
    for parent in shape.iterancestors():
        if parent.tag!='{%s}grpSp'%NS['p']:continue
        transform=parent.find('p:grpSpPr/a:xfrm',NS)
        if transform is None:continue
        o,e,co,ce=(transform.find('a:'+key,NS) for key in ('off','ext','chOff','chExt'))
        if any(v is None for v in (o,e,co,ce)):return None
        # Rotated groups need a full transformed box; do not guess their footer location.
        if transform.get('rot','0')!='0':return None
        sx=float(e.get('cx',0))/max(1,float(ce.get('cx',0)))
        sy=float(e.get('cy',0))/max(1,float(ce.get('cy',0)))
        x=float(o.get('x',0))+(x-float(co.get('x',0)))*sx
        y=float(o.get('y',0))+(y-float(co.get('y',0)))*sy
    return x/12700,y/12700

def _replace_spans(texts, replacements):
    # Work backwards in the original string, preserving styles of each run and
    # all separator/prefix characters, even when a number is split across runs.
    for start,end,value in sorted(replacements,reverse=True):
        offset=0;inserted=False
        for node in texts:
            text=node.text or '';length=len(text)
            left,right=max(start-offset,0),min(end-offset,length)
            if left<right:
                node.text=text[:left]+(value if not inserted else '')+text[right:]
                inserted=True
            offset+=length

def _padding_key(shape, original, width, height):
    position=_position(shape)
    if position is None:return None
    return (re.sub(r'\d+','#',original),round(position[0]/width*100),round(position[1]/height*100))

def pagination_padding(roots, width, height):
    """Infer leading-zero style from sibling footers, including pages above 9."""
    result={};series={};upper_series={}
    for index,root in enumerate(roots):
        for shape in root.findall('.//p:sp',NS):
            original=''.join(t.text or '' for t in shape.findall('p:txBody//a:t',NS))
            match=PAGINATION.fullmatch(original);position=_position(shape)
            if not match or position is None:continue
            current=match.group('current')
            key=_padding_key(shape,original,width,height)
            sizes=[int(n.get('sz'))/100 for n in shape.findall('p:txBody//*[@sz]',NS) if n.get('sz','').isdigit()]
            # A repeated large sidebar ordinal is furniture too. A lone number,
            # an interior KPI or a repeated constant is not sufficient evidence.
            if position[1]<height*.18 and position[0]<width*.18 and re.fullmatch(r'\s*\d{1,3}\s*',original) and sizes:
                upper_series.setdefault(key,[]).append((index,int(current),max(sizes)))
                if len(current)>1 and current.startswith('0'):result[key]=max(result.get(key,0),len(current))
            if position[1]<height*.9:continue
            if sizes and max(sizes)<=max(20,height*.04):
                series.setdefault(key,[]).append((index,int(current),match.group('total')))
            if len(current)>1 and current.startswith('0'):
                result[key]=max(result.get(key,0),len(current))
    # A consistent offset across several source pages proves pagination even
    # in an excerpt (e.g. pages 6..20 of a larger deck). Unrelated footer
    # statistics or repeated dates do not form this sequence.
    for key,values in series.items():
        if (len(values)>=3 and len({i for i,_,_ in values})==len(values)
            and len({t for _,_,t in values})==1
            and (len({n-i for i,n,_ in values})==1
                 or (key[1]>=70 and len({n for _,n,_ in values})==len(values))
                 or (all(t and 0<n<=int(t) for _,n,t in values)
                     and (all(b[1]>a[1] for a,b in zip(values,values[1:]))
                          or (key[1]>=70 and len({n for _,n,_ in values})==len(values)))))):
            result[('sequence',)+key]=True
    for key,values in upper_series.items():
        if len(values)>=4 and len({i for i,_,_ in values})==len(values) and len({n-i for i,n,_ in values})==1 and max(s for _,_,s in values)-min(s for _,_,s in values)<.2:
            result[('upper_sequence',)+key]=True
    return result

def upper_page_marker(shape,width,height,padding):
    text=''.join(t.text or '' for t in shape.findall('p:txBody//a:t',NS))
    key=_padding_key(shape,text,width,height)
    return bool(key and padding.get(('upper_sequence',)+key))

def expected_page_fields(root,width,height,native,source_index,total,padding,explicit_ids=()):
    """Derive QA expectations from source pagination, never from output text."""
    from copy import deepcopy
    if not native.get('preserveTemplate'):return {}
    clone=deepcopy(root)
    def values():
        return {int(s.find('p:nvSpPr/p:cNvPr',NS).get('id')):''.join(t.text or '' for t in s.findall('p:txBody//a:t',NS))
                for s in clone.findall('.//p:sp',NS) if s.find('p:nvSpPr/p:cNvPr',NS) is not None}
    before=values();ordinal=native.get('ordinal',source_index+1)
    for identity in explicit_ids:
        for shape in clone.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(identity)):
            for i,t in enumerate(shape.findall('p:txBody//a:t',NS)):t.text=str(ordinal) if i==0 else ''
    renumber_pages(clone,width,height,[native.get('sourceOrdinal'),source_index+1],ordinal,native.get('deckSlideCount',total),native.get('sourceSlideCount'),padding)
    return {key:value for key,value in values().items() if value!=before[key] or key in explicit_ids}

def renumber_pages(root, width_pt, height_pt, source_ordinal, ordinal, total=None, source_total=None, padding=None):
    sources=set(source_ordinal if isinstance(source_ordinal,(list,tuple,set)) else [source_ordinal])
    for shape in root.findall('.//p:sp',NS):
        texts=shape.findall('p:txBody//a:t',NS)
        original=''.join(t.text or '' for t in texts)
        placeholder=shape.find('p:nvSpPr/p:nvPr/p:ph',NS)
        explicit=placeholder is not None and placeholder.get('type')=='sldNum'
        explicit=explicit or any(f.get('type','').lower()=='slidenum' for f in shape.findall('p:txBody//a:fld',NS))
        match=PAGINATION.fullmatch(original)
        if not texts or not match:continue
        current=match.group('current');old_total=match.group('total')
        position=_position(shape)
        sizes=[int(n.get('sz'))/100 for n in shape.findall('p:txBody//*[@sz]',NS) if n.get('sz','').isdigit()]
        small=bool(sizes) and max(sizes)<=max(20,height_pt*.04)
        bottom=position is not None and position[1]>=height_pt*.9
        right=position is not None and position[0]>=width_pt*.7
        # A matching original page index or an explicit page label is evidence;
        # a plain unrelated footer statistic must remain untouched.
        labelled=bool(re.match(r'^\s*(?:слайд|стр\.?|страница|slide|page|p\.?|№)\s*\d',original,re.I))
        fraction=old_total and 0<int(current)<=int(old_total) and right and (int(current) in sources or int(old_total)==source_total or source_total is None)
        sequence=(padding or {}).get(('sequence',)+_padding_key(shape,original,width_pt,height_pt),False) if position else False
        upper=upper_page_marker(shape,width_pt,height_pt,padding or {})
        if not (explicit or upper or (bottom and small and (int(current) in sources or labelled or fraction or sequence))):continue
        digits=(padding or {}).get(_padding_key(shape,original,width_pt,height_pt),0)
        padded=digits or (len(current) if len(current)>1 and current.startswith('0') else 0)
        value=str(ordinal).zfill(padded) if padded else str(ordinal)
        changes=[(*match.span('current'),value)]
        if old_total and total is not None:
            total_padded=padded or (len(old_total)>1 and old_total.startswith('0'))
            changes.append((*match.span('total'),str(total).zfill(len(old_total)) if total_padded else str(total)))
        _replace_spans(texts,changes)
