"""Recover usable space inside native cells without changing the table grid."""
from copy import deepcopy
from runtime.security import NS
from runtime.text_frames import TemplateTextFrame

EMU=12700
MARGINS=('left_emu','right_emu','top_emu','bottom_emu')

def expand_cell_padding(layout,frames,values,fitter):
    result=deepcopy(frames);changes={}
    def fit(frame,text):return fitter.fit(TemplateTextFrame.model_validate(frame),text.split('\n')).status
    for slot in layout['slots']:
        key=slot['key'];text=values.get(key,'')
        if 'cell' not in slot or not text.strip():continue
        original=frames[key]
        if fit(original,text)!='overflow':continue
        # Leave at least 2 pt of whitespace; never increase a smaller original inset.
        before={k:original.get(k,0) or 0 for k in MARGINS}
        minimum={k:min(v,2*EMU) for k,v in before.items()}
        def candidate(ratio):
            return {**original,**{k:round(minimum[k]+(before[k]-minimum[k])*ratio) for k in MARGINS}}
        if fit(candidate(0),text)!='fits':continue
        low,high=0.,1.
        for _ in range(12):
            mid=(low+high)/2
            if fit(candidate(mid),text)=='fits':low=mid
            else:high=mid
        # Tiny safety reserve avoids landing precisely on a raster rounding boundary.
        fitted=candidate(max(0,low-.02))
        result[key]=fitted
        bounds={k:slot[k] for k in ('x','y','w','h')}
        changes[key]=dict(shapeId=slot['shapeId'],cell=slot['cell'],**bounds,before=bounds,
            paddingBefore=before,padding={k:fitted[k] for k in MARGINS},reason='source_table_cell_padding')
    return result,changes

def apply_cell_padding(root,changes):
    for change in changes.values():
        if 'padding' not in change:continue
        nodes=root.xpath('./p:cSld/p:spTree/p:graphicFrame[p:nvGraphicFramePr/p:cNvPr/@id=$id]',namespaces=NS,id=str(change['shapeId']))
        if len(nodes)!=1:raise ValueError('pptx_padding_table_changed')
        row,col=change['cell'];cell=nodes[0].findall('.//a:tbl/a:tr',NS)[row].findall('a:tc',NS)[col]
        props=cell.find('a:tcPr',NS)
        if props is None:
            from lxml import etree
            props=etree.SubElement(cell,'{%s}tcPr'%NS['a'])
        for name,attribute in zip(MARGINS,('marL','marR','marT','marB')):
            props.set(attribute,str(change['padding'][name]))
