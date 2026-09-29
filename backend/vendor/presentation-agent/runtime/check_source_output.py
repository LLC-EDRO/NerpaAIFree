"""Read-only structural audit of a generated strict-source PPTX.

Usage: python -m runtime.check_source_output prepared.pptx output.pptx manifest.json slides.json
No source/result files or database rows are modified.
"""
import json
import io
import posixpath
import sys
from copy import deepcopy
from pathlib import Path
from PIL import Image
from runtime.security import NS, xml, read_package


def canonical(node):
    if node is None:
        return None
    return (node.tag, tuple(sorted(node.attrib.items())), (node.text or '').strip(), tuple(canonical(c) for c in node))


def shapes(root):
    return [(n.get('id'), n.getparent().getparent()) for n in root.findall('.//p:cSld/p:spTree//p:cNvPr', NS)]


def properties(node, picture_fill=False):
    props=[]
    for tag in ('p:spPr', 'p:grpSpPr', 'p:xfrm', 'p:style'):
        child=node.find(tag,NS)
        if child is not None:
            child=deepcopy(child)
            if picture_fill:
                for fill in list(child):
                    if fill.tag.split('}')[-1] in ('noFill','solidFill','gradFill','blipFill','pattFill','grpFill'):
                        child.remove(fill)
            props.append(canonical(child))
    return props


def cleared_placeholder_fill(before, after, parts, slide_part):
    """Only an empty native photo placeholder may lose its sample fill.
    Verify actual zero-alpha pixels; this is not permission to restyle shapes.
    Geometry, masks, lines, effects and identity are still checked separately.
    """
    ph=before.find('p:nvSpPr/p:nvPr/p:ph',NS)
    if ph is None or ph.get('type')!='pic' or any(t.text and t.text.strip() for t in before.findall('.//a:t',NS)):
        return False
    blip=after.find('p:spPr/a:blipFill/a:blip',NS)
    if blip is None:return False
    rel_part=posixpath.join(posixpath.dirname(slide_part),'_rels',posixpath.basename(slide_part)+'.rels')
    if rel_part not in parts:return False
    rel=next((r for r in xml(parts[rel_part]) if r.get('Id')==blip.get('{%s}embed'%NS['r'])),None)
    if rel is None or rel.get('TargetMode')=='External':return False
    target=posixpath.normpath(posixpath.join(posixpath.dirname(slide_part),rel.get('Target','')))
    if target not in parts:return False
    try:
        with Image.open(io.BytesIO(parts[target])) as picture:
            return picture.convert('RGBA').getchannel('A').getextrema()==(0,0)
    except (OSError,ValueError):
        return False


def audit(source, output, manifest, slides, folder=None):
    original=read_package(Path(source).read_bytes())
    result=read_package(Path(output).read_bytes())
    layouts={l['id']:l for l in manifest['userTemplate']['layouts']}
    report=[]
    expansions={}
    if any(s['native'].get('safeTextExpansion') is True for s in slides):
        assert folder is not None,'Expansion audit requires the original analyzed template folder'
        from runtime.analysis import validate_fields
        assert not validate_fields(Path(folder),slides,expansions),'Expanded content must pass native fit'
    for index,slide in enumerate(slides):
        native=slide['native'];assert native.get('mode')=='source'
        assert not any(native.get(k) for k in ('composition','tableRows','tableRowWeights','textSizes'))
        layout=layouts[native['sourceSlideId']]
        before=xml(original[layout['part']]);after=xml(result[f'ppt/slides/nerpaSlide{index+1}.xml'])
        if expansions.get(index):
            from runtime.safe_text_expansion import apply_expansion
            apply_expansion(before,expansions[index])
        a=shapes(before);b=shapes(after)
        assert [i for i,_ in a]==[i for i,_ in b],f'Slide {index+1}: shape identity/order changed'
        assert canonical(before.find('p:cSld/p:bg',NS))==canonical(after.find('p:cSld/p:bg',NS)),f'Slide {index+1}: background changed'
        for (identity,left),(_,right) in zip(a,b):
            assert left.tag==right.tag,f'Slide {index+1}, {identity}: object type changed'
            replacement=any(str(p['shapeId'])==identity for p in slide['images']) if 'images' in slide else bool(slide.get('image')) and str(layout.get('imageShapeId'))==identity
            clear_placeholder=index>0 and cleared_placeholder_fill(left,right,result,f'ppt/slides/nerpaSlide{index+1}.xml')
            assert properties(left,replacement or clear_placeholder)==properties(right,replacement or clear_placeholder),f'Slide {index+1}, {identity}: geometry/style changed'
            for name in ('p:nvSpPr','p:nvPicPr','p:nvGraphicFramePr','p:nvCxnSpPr','p:nvGrpSpPr'):
                assert canonical(left.find(name,NS))==canonical(right.find(name,NS)),f'Slide {index+1}, {identity}: native metadata changed'
            for ltable,rtable in zip(left.findall('.//a:tbl',NS),right.findall('.//a:tbl',NS)):
                assert canonical(ltable.find('a:tblGrid',NS))==canonical(rtable.find('a:tblGrid',NS))
                assert [r.get('h') for r in ltable.findall('a:tr',NS)]==[r.get('h') for r in rtable.findall('a:tr',NS)]
                assert [len(r.findall('a:tc',NS)) for r in ltable.findall('a:tr',NS)]==[len(r.findall('a:tc',NS)) for r in rtable.findall('a:tr',NS)]
        report.append(dict(slide=index+1,source=layout['id'],sourceObjects=len(a),outputObjects=len(b),geometryUnchanged=not bool(expansions.get(index)),safeExpandedFields=list(expansions.get(index,{}))))
    # Theme/media are reused verbatim. New generated image parts are additional;
    # the original backgrounds, logos and artwork must never be overwritten.
    for name,data in original.items():
        if name.startswith(('ppt/theme/','ppt/media/')):
            assert result.get(name)==data,f'Original theme/media overwritten: {name}'
    return dict(passed=True,slides=report,originalMediaUnchanged=True,themesUnchanged=True)


if __name__=='__main__':
    source,output,manifest,slides,*folders=sys.argv[1:]
    print(json.dumps(audit(source,output,json.loads(Path(manifest).read_text()),json.loads(Path(slides).read_text()),folders[0] if folders else None),ensure_ascii=False))
