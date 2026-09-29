"""Render stored v1 composition objects only, constrained by their source envelope."""
import hashlib
import io
import math
import re
from lxml import etree
from PIL import Image
from runtime.security import NS
from runtime.geometry import inside, intersection
from runtime.text_frames import TemplateTextFrame, TemplateParagraph, TemplateTextFitter
from runtime.charts import workbook


def node(parent, element, **attrs):
    prefix,tag=element.split(':')
    return etree.SubElement(parent,'{%s}%s'%(NS[prefix],tag),{k:str(v) for k,v in attrs.items()})


def geometry(parent, box, tag='a:xfrm'):
    x=node(parent,tag)
    node(x,'a:off',x=round(box['x']*12700),y=round(box['y']*12700))
    node(x,'a:ext',cx=round(box['w']*12700),cy=round(box['h']*12700))


def solid(parent, color):
    node(node(parent,'a:solidFill'),'a:srgbClr',val=color.lstrip('#'))


def text_frame(spec):
    return TemplateTextFrame(source_element_id=spec['key'],source_fingerprint='adaptive-v1',
        width_emu=round(spec['w']*12700),height_emu=round(spec['h']*12700),
        left_emu=0,right_emu=0,top_emu=0,bottom_emu=0,autofit_ink_bounds=True,
        paragraphs=[TemplateParagraph(has_text=True,font_family=spec['font'],font_size_pt=spec['size'],
            bold=spec['bold'],italic=False,line_spacing=('percent',110))])


def validate_composition(profile, native, index):
    c=native['composition']; fields=native['fields']
    if not profile or c.get('version')!=1 or c.get('fingerprint')!=profile['fingerprint']: raise ValueError('pptx_design_profile_mismatch')
    texts=c.get('texts',[]); panels=c.get('panels',[]); chart=c.get('chart'); image=c.get('imageBox')
    if not 1<=len(texts)<=30 or len(panels)>12: raise ValueError('pptx_composition_limit')
    expected={s.get('key') for s in texts}
    if len(expected)!=len(texts) or 'a_title' not in expected: raise ValueError('pptx_composition_keys')
    if chart:
        points=chart.get('points',[])
        if not 2<=len(points)<=6: raise ValueError('pptx_composition_chart')
        expected.update(k for p in points for k in (p['labelKey'],p['valueKey']))
        if chart.get('unitKey') not in expected or chart.get('sourceKey') not in expected: raise ValueError('pptx_composition_chart')
        for p in points:
            try: value=float(fields[p['valueKey']])
            except (ValueError,KeyError,TypeError): raise ValueError('pptx_composition_chart')
            if not math.isfinite(value) or abs(value)>1e15 or not fields[p['labelKey']].strip() or len(fields[p['labelKey']])>40: raise ValueError('pptx_composition_chart')
    if set(fields)!=expected or any(not isinstance(k,str) or not re.fullmatch('a_[a-zA-Z0-9]+',k) for k in expected): raise ValueError('pptx_fields_mismatch')
    if any(not isinstance(v,str) or len(v)>4000 for v in fields.values()): raise ValueError('pptx_invalid_text')
    style=profile['style']; fonts={style['font'],style['headingFont']}
    colors={style[k] for k in ('background','ink','accent','onAccent','surface','muted','titleColor')}
    all_boxes=[*texts,*panels,*([chart] if chart else []),*([image] if image else [])]
    for b in all_boxes:
        if any(not isinstance(b.get(k),(int,float)) or not math.isfinite(b[k]) for k in ('x','y','w','h')) or b['w']<=0 or b['h']<=0: raise ValueError('pptx_composition_geometry')
        if not inside(b,profile['titleBox'] if b.get('key')=='a_title' else profile['bodyBox']): raise ValueError('pptx_composition_protected_area')
    if image and not profile['canAddImage']: raise ValueError('pptx_composition_image_capacity')
    for p in panels:
        if p['color'] not in colors: raise ValueError('pptx_composition_palette')
    foreground=[*texts,*([chart] if chart else []),*([image] if image else [])]
    for i,a in enumerate(foreground):
        for b in foreground[i+1:]:
            if intersection(a,b)>.5: raise ValueError('pptx_composition_overlap')
    fitter=TemplateTextFitter();issues=[]
    for s in texts:
        if s['font'] not in fonts or s['color'] not in colors or not 11<=s['size']<=max(72,style['titleSize']) or s.get('align') not in ('left','center','right'): raise ValueError('pptx_composition_typography')
        value=fields[s['key']]
        if not value.strip(): continue
        report=fitter.fit(text_frame(s),value.split('\n'))
        if report.status!='fits':
            ratio=min(1,report.available_width_emu/max(1,report.measured_width_emu),report.available_height_emu/max(1,report.measured_height_emu))
            issues.append(dict(slide=index,key=s['key'],reason=report.status,details=report.reason_codes,
                **(dict(suggestedMaxChars=max(1,math.floor(len(value)*min(.75,ratio*.85)))) if report.status=='overflow' else {})))
    return issues


def add_shape(tree, shape_id, name, box, color=None):
    shape=node(tree,'p:sp');nv=node(shape,'p:nvSpPr');node(nv,'p:cNvPr',id=shape_id,name=name)
    node(nv,'p:cNvSpPr');node(nv,'p:nvPr');props=node(shape,'p:spPr');geometry(props,box)
    node(node(props,'a:prstGeom',prst='rect'),'a:avLst')
    solid(props,color) if color else node(props,'a:noFill')
    node(node(props,'a:ln'),'a:noFill')
    return shape


def add_text(tree, shape_id, spec, value):
    shape=add_shape(tree,shape_id,'Nerpa '+spec['key'],spec)
    body=node(shape,'p:txBody');bp=node(body,'a:bodyPr',wrap='square',lIns=0,rIns=0,tIns=0,bIns=0,anchor={'top':'t','center':'ctr','bottom':'b'}.get(spec.get('verticalAlign'),'ctr' if spec['key']=='a_title' else 't'))
    node(bp,'a:noAutofit');node(body,'a:lstStyle')
    for line in value.split('\n'):
        p=node(body,'a:p');pp=node(p,'a:pPr',algn={'left':'l','center':'ctr','right':'r'}[spec['align']],marL=0,indent=0)
        node(node(pp,'a:lnSpc'),'a:spcPct',val=110000);node(pp,'a:buNone')
        run=node(p,'a:r');r=node(run,'a:rPr',lang='ru-RU',sz=round(spec['size']*100),b=int(spec['bold']))
        solid(r,spec['color'])
        for tag in ('a:latin','a:ea','a:cs'):node(r,tag,typeface=spec['font'])
        node(run,'a:t').text=line


def add_picture(tree,shape_id,box,payload,result,rels,index):
    name='ppt/media/nerpa-'+hashlib.sha256(payload).hexdigest()+'.png';result[name]=payload
    rid='nerpaAdaptiveImage'+str(index)
    node(rels,'pr:Relationship',Id=rid,Type=NS['r']+'/image',Target='../media/'+name.split('/')[-1])
    pic=node(tree,'p:pic');nv=node(pic,'p:nvPicPr');node(nv,'p:cNvPr',id=shape_id,name='Nerpa generated illustration')
    node(node(nv,'p:cNvPicPr'),'a:picLocks',noChangeAspect=1);node(nv,'p:nvPr')
    fill=node(pic,'p:blipFill');blip=node(fill,'a:blip');blip.set('{%s}embed'%NS['r'],rid)
    with Image.open(io.BytesIO(payload)) as im: ratio=im.width/im.height
    target=box['w']/box['h'];horizontal=round(max(0,(1-target/ratio)*50000));vertical=round(max(0,(1-ratio/target)*50000))
    node(fill,'a:srcRect',l=horizontal,r=horizontal,t=vertical,b=vertical)
    node(node(fill,'a:stretch'),'a:fillRect');props=node(pic,'p:spPr');geometry(props,box);node(node(props,'a:prstGeom',prst='rect'),'a:avLst')


def chart_font(parent,style,size=14):
    t=node(parent,'c:txPr');node(t,'a:bodyPr');node(t,'a:lstStyle');p=node(t,'a:p');pp=node(p,'a:pPr');r=node(pp,'a:defRPr',sz=round(size*100))
    solid(r,style['ink']);node(r,'a:latin',typeface=style['font']);node(p,'a:endParaRPr',lang='ru-RU')


def add_chart(tree,shape_id,chart,fields,style,result,rels,index,content_types):
    values=[float(fields[p['valueKey']]) for p in chart['points']];labels=[fields[p['labelKey']] for p in chart['points']]
    data=dict(title=fields[chart['unitKey']],categories=labels,series=[dict(name=fields[chart['unitKey']],values=values)])
    part=f'ppt/charts/nerpaAdaptive{index+1}.xml';book=f'ppt/embeddings/nerpaAdaptive{index+1}.xlsx'
    root=etree.Element('{%s}chartSpace'%NS['c'],nsmap={'c':NS['c'],'a':NS['a'],'r':NS['r']})
    node(root,'c:lang',val='ru-RU');c=node(root,'c:chart');node(c,'c:autoTitleDeleted',val=1)
    plot=node(c,'c:plotArea');node(plot,'c:layout');bar=node(plot,'c:barChart');node(bar,'c:barDir',val='bar');node(bar,'c:grouping',val='clustered');node(bar,'c:varyColors',val=0)
    series=node(bar,'c:ser');node(series,'c:idx',val=0);node(series,'c:order',val=0)
    node(node(series,'c:tx'),'c:v').text=data['title']
    solid(node(series,'c:spPr'),style['accent'])
    for tag,kind,formula,vals in [('cat','str','Data!$A$2:$A$'+str(len(labels)+1),labels),('val','num','Data!$B$2:$B$'+str(len(values)+1),values)]:
        ref=node(node(series,'c:'+tag),'c:'+kind+'Ref');node(ref,'c:f').text=formula;cache=node(ref,'c:'+kind+'Cache')
        if kind=='num':node(cache,'c:formatCode').text='General'
        node(cache,'c:ptCount',val=len(vals))
        for i,v in enumerate(vals):node(node(cache,'c:pt',idx=i),'c:v').text=str(v)
    labels_node=node(bar,'c:dLbls');chart_font(labels_node,style,15);node(labels_node,'c:dLblPos',val='outEnd');node(labels_node,'c:showLegendKey',val=0);node(labels_node,'c:showVal',val=1);node(labels_node,'c:showCatName',val=0);node(labels_node,'c:showSerName',val=0)
    node(bar,'c:gapWidth',val=85)
    node(bar,'c:axId',val=100);node(bar,'c:axId',val=200)
    for kind,identity,position,cross in [('catAx',100,'l',200),('valAx',200,'b',100)]:
        ax=node(plot,'c:'+kind);node(ax,'c:axId',val=identity);scale=node(ax,'c:scaling');node(scale,'c:orientation',val='maxMin' if kind=='catAx' else 'minMax')
        if kind=='valAx':
            # Length encodes magnitude: never exaggerate a small difference by
            # silently truncating the bar-chart baseline.
            if max(values)<=0:node(scale,'c:max',val=0)
            node(scale,'c:min',val=min(0,min(values)))
        node(ax,'c:delete',val=0);node(ax,'c:axPos',val=position);node(ax,'c:majorTickMark',val='none');node(ax,'c:minorTickMark',val='none');node(ax,'c:tickLblPos',val='nextTo')
        node(node(node(ax,'c:spPr'),'a:ln'),'a:noFill');chart_font(ax,style,14)
        node(ax,'c:crossAx',val=cross);node(ax,'c:crosses',val='autoZero' if kind=='catAx' else 'max')
        if kind=='catAx':node(ax,'c:lblOffset',val=100)
    node(c,'c:plotVisOnly',val=1);node(c,'c:dispBlanksAs',val='gap');node(node(root,'c:spPr'),'a:noFill');chart_font(root,style)
    ext=node(root,'c:externalData');ext.set('{%s}id'%NS['r'],'data');node(ext,'c:autoUpdate',val=0)
    result[part]=etree.tostring(root,xml_declaration=True,encoding='UTF-8')
    cr=etree.Element('{%s}Relationships'%NS['pr']);node(cr,'pr:Relationship',Id='data',Type=NS['r']+'/package',Target='../embeddings/'+book.split('/')[-1])
    result['ppt/charts/_rels/'+part.split('/')[-1]+'.rels']=etree.tostring(cr,xml_declaration=True,encoding='UTF-8');result[book]=workbook(data)
    node(content_types,'ct:Override',PartName='/'+part,ContentType='application/vnd.openxmlformats-officedocument.drawingml.chart+xml')
    if not any(n.get('Extension')=='xlsx' for n in content_types):node(content_types,'ct:Default',Extension='xlsx',ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    rid='nerpaAdaptiveChart'+str(index);node(rels,'pr:Relationship',Id=rid,Type=NS['r']+'/chart',Target='../charts/'+part.split('/')[-1])
    frame=node(tree,'p:graphicFrame');nv=node(frame,'p:nvGraphicFramePr');node(nv,'p:cNvPr',id=shape_id,name='Nerpa editable chart');node(nv,'p:cNvGraphicFramePr');node(nv,'p:nvPr');geometry(frame,chart,'p:xfrm')
    gd=node(node(frame,'a:graphic'),'a:graphicData',uri=NS['c']);node(gd,'c:chart').set('{%s}id'%NS['r'],rid)


def apply_composition(root,profile,native,image,result,rels,index,content_types):
    c=native['composition'];tree=root.find('p:cSld/p:spTree',NS)
    for identity in profile['removeShapeIds']:
        for nv in root.xpath('.//p:cNvPr[@id=$id]',namespaces=NS,id=str(identity)):
            shape=nv.getparent().getparent()
            if identity==profile.get('preserveTitleShapeId'):
                for text in shape.findall('p:txBody//a:t',NS):text.text=''
            else:shape.getparent().remove(shape)
    # Timing targets can point to content we removed. Do not retain dangling animations.
    for timing in root.findall('p:timing',NS):root.remove(timing)
    shape_id=max([int(n.get('id')) for n in root.findall('.//p:cNvPr',NS)]+[1])+1
    for p in c['panels']:
        add_shape(tree,shape_id,'Nerpa accent',p,p['color']);shape_id+=1
    if c.get('imageBox'):
        if image:add_picture(tree,shape_id,c['imageBox'],image,result,rels,index)
        else:add_shape(tree,shape_id,'Pending illustration',c['imageBox'],profile['style']['surface'])
        shape_id+=1
    if c.get('chart'):
        add_chart(tree,shape_id,c['chart'],native['fields'],profile['style'],result,rels,index,content_types);shape_id+=1
    for spec in c['texts']:
        add_text(tree,shape_id,spec,native['fields'][spec['key']]);shape_id+=1
