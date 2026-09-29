"""Assemble chosen native slides without rewriting shapes or text.

Repeated selections get independent slide parts. Native charts are cloned later
by fill_chart; shared immutable media/themes remain shared. Notes on selected
slides are retained here as part of the original layout, removed at final fill.
"""
import json, hashlib, posixpath, zipfile
from lxml import etree
from runtime.security import NS, xml, read_package
from app.presentation.parser.archive import relationship_part
from runtime.fill import serialize
from runtime.package_cleanup import compact_package
from runtime.fonts import effective_source
from runtime.page_numbers import renumber_pages, pagination_padding

def assemble(source, folder, ids, output):
    analysis=json.loads((folder/'analysis.json').read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest()!=analysis['sha256']:raise ValueError('pptx_source_changed')
    layouts={l['id']:l for l in analysis['layouts']}
    if not isinstance(ids,list) or not 1<=len(ids)<=20 or any(i not in layouts for i in ids):raise ValueError('pptx_invalid_selection')
    parts=read_package(effective_source(source,folder,analysis).read_bytes())
    padding=pagination_padding((xml(parts[l["part"]]) for l in layouts.values()),analysis["width"],analysis["height"])
    result=dict(parts)
    pres=xml(parts['ppt/presentation.xml']);rels=xml(parts['ppt/_rels/presentation.xml.rels']);ct=xml(parts['[Content_Types].xml'])
    listing=pres.find('p:sldIdLst',NS)
    for child in list(listing):listing.remove(child)
    for child in list(rels):
        if child.get('Type','').endswith('/slide'):rels.remove(child)
    for child in pres.findall('p:custShowLst',NS):pres.remove(child)
    mapping={}
    for index,identity in enumerate(ids):mapping.setdefault(layouts[identity]['part'],f'ppt/slides/labSlide{index+1}.xml')
    for index,identity in enumerate(ids):
        original=layouts[identity]['part'];part=f'ppt/slides/labSlide{index+1}.xml'
        root=xml(parts[original]);root.attrib.pop('show',None)
        renumber_pages(root,analysis['width'],analysis['height'],layouts[identity]['index']+1,index+1,len(ids),len(layouts),padding)
        relpart=relationship_part(original)
        sr=xml(parts[relpart]) if relpart in parts else etree.Element('{%s}Relationships'%NS['pr'])
        for rel in list(sr):
            kind=rel.get('Type','').split('/')[-1]
            # Avoid old notes/comments retaining unselected slide references.
            if kind in ('notesSlide','comments','commentAuthors'):sr.remove(rel)
            elif kind=='slide':
                target=posixpath.normpath(posixpath.join(posixpath.dirname(original),rel.get('Target')))
                if target in mapping:rel.set('Target',posixpath.relpath(mapping[target],posixpath.dirname(part)))
                else:
                    for link in root.xpath('.//*[@r:id=$id]',namespaces=NS,id=rel.get('Id')):link.getparent().remove(link)
                    sr.remove(rel)
        result[part]=serialize(root);result[relationship_part(part)]=serialize(sr)
        rid=f'labSlide{index+1}'
        etree.SubElement(rels,'{%s}Relationship'%NS['pr'],Id=rid,Type=NS['r']+'/slide',Target=posixpath.relpath(part,'ppt'))
        etree.SubElement(listing,'{%s}sldId'%NS['p'],{'id':str(256+index),'{%s}id'%NS['r']:rid})
        etree.SubElement(ct,'{%s}Override'%NS['ct'],PartName='/'+part,ContentType='application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
    result['ppt/presentation.xml']=serialize(pres);result['ppt/_rels/presentation.xml.rels']=serialize(rels);result['[Content_Types].xml']=serialize(ct)
    result,_=compact_package(result)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in result.items():archive.writestr(name,data)
    return dict(slideCount=len(ids),sourceSha256=analysis['sha256'])
