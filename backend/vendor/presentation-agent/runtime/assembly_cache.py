"""Select the immutable parsed slides; never parse or render the source again."""
import hashlib
import json
from copy import deepcopy
from runtime.model_store import read_model, write_model


def select_cached_analysis(folder, ids, output):
    data=json.loads((folder/'analysis.json').read_text())
    model=read_model(folder/('effective-parser.json' if (folder/'effective-parser.json').exists() else 'parser.json'))
    analyzer=read_model(folder/'analyzer.json')
    frames=json.loads((folder/'frames.json').read_text())
    layouts={v['id']:v for v in data['layouts']}
    slides={v['slide_id']:v for v in model['slides']}
    assignments={v['slide_id']:v for v in analyzer['slide_assignments']}
    selected=[]; parsed=[]; selected_frames={}; selected_assignments=[]; tables=[]
    for index, identity in enumerate(ids):
        original=layouts[identity]; new_id=f'selected_{index+1}'; part=f'ppt/slides/labSlide{index+1}.xml'
        def remap(value):
            if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
            if isinstance(value,list):return [remap(v) for v in value]
            if isinstance(value,str):
                if value==identity or value.startswith(identity+'_'):return new_id+value[len(identity):]
                if value==original['part']:return part
                if value.startswith('slide|'+original['part']+'|'):return value.replace(original['part'],part).replace('|'+identity+'_','|'+new_id+'_')
            return value
        layout=remap(original);layout.update(id=new_id,index=index,part=part)
        selected.append(layout)
        slide=remap(slides[identity]);slide.update(slide_id=new_id,slide_index=index,source_part=part);parsed.append(slide)
        selected_frames[new_id]=remap(frames[identity])
        if identity in assignments:selected_assignments.append(remap(assignments[identity]))
        tables.extend(remap(t) for t in model.get('tables',[]) if t.get('source_part')==original['part'])
    # Downstream native operations consume these exact model components. Keep
    # shared theme/master/layout definitions; omit all unselected slide payloads.
    retained={k:deepcopy(model[k]) for k in ('schema_version','presentation_id','dimensions','theme','masters','layouts','assets') if k in model}
    retained.update(slides=parsed,tables=tables)
    write_model(output/'effective-parser.json',retained)
    write_model(output/'analyzer.json',{'slide_assignments':selected_assignments})
    (output/'frames.json').write_text(json.dumps(selected_frames,ensure_ascii=False,separators=(',',':')))
    result={**data,'layouts':selected,'sha256':hashlib.sha256((output/'presentation.pptx').read_bytes()).hexdigest()}
    result.pop('preparedSha256',None)
    (output/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    if (folder/'previews'/'presentation.pdf').exists():
        import pymupdf
        with pymupdf.open(folder/'previews'/'presentation.pdf') as source, pymupdf.open() as selected_pdf:
            for identity in ids:
                page=layouts[identity]['index'];selected_pdf.insert_pdf(source,from_page=page,to_page=page)
            selected_pdf.save(output/'source-preview.pdf')
    return result
