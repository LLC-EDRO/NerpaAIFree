"""Clone standard native category/value charts and supply a fresh editable workbook."""
import io
import math
import posixpath
import zipfile
from copy import deepcopy
from lxml import etree
from runtime.security import NS,xml
from app.presentation.parser.archive import relationship_part

NS['c']='http://schemas.openxmlformats.org/drawingml/2006/chart'

def chart_spec(item,parts):
    part=item.media_id
    if not part or part not in parts: raise ValueError('pptx_chart_part_missing')
    root=xml(parts[part]);series=root.xpath('.//c:plotArea/*/c:ser',namespaces=NS)
    if root.xpath('.//c:logBase',namespaces=NS):raise ValueError('pptx_chart_scale_unsupported')
    kinds={etree.QName(s.getparent()).localname for s in series}
    if not kinds or not kinds.issubset({'barChart','lineChart','areaChart','pieChart','doughnutChart','radarChart'}): raise ValueError('pptx_chart_type_unsupported')
    if not 1<=len(series)<=6: raise ValueError('pptx_chart_series_limit')
    counts=[]
    for s in series:
        cat=s.find('c:cat',NS);val=s.find('c:val',NS)
        if cat is None or val is None or len(cat.findall('c:multiLvlStrRef/c:multiLvlStrCache/c:lvl',NS))>1: raise ValueError('pptx_chart_data_unsupported')
        values=val.xpath('.//c:pt',namespaces=NS);labels=cat.xpath('.//c:pt',namespaces=NS)
        if not 1<=len(values)<=12 or len(values)!=len(labels): raise ValueError('pptx_chart_data_unsupported')
        counts.append(len(values))
    if len(set(counts))!=1: raise ValueError('pptx_chart_categories_mismatch')
    return dict(key=f'chart{item.shape_id}',shapeId=item.shape_id,part=part,type='/'.join(sorted(kinds)),seriesCount=len(series),pointCount=counts[0],hasTitle=root.find('c:chart/c:title',NS) is not None)

def validate_charts(layout,native):
    expected={c['key']:c for c in layout.get('charts',[])}
    values=native.get('charts',{})
    if set(values)!=set(expected): raise ValueError('pptx_chart_keys_mismatch')
    for key,spec in expected.items():
        value=values[key]
        if not isinstance(value.get('title',''),str) or len(value.get('title',''))>80: raise ValueError('pptx_chart_title_invalid')
        if len(value.get('categories',[]))!=spec['pointCount'] or any(not isinstance(t,str) or not t.strip() or len(t)>30 for t in value['categories']): raise ValueError('pptx_chart_categories_invalid')
        if len(value.get('series',[]))!=spec['seriesCount']: raise ValueError('pptx_chart_series_invalid')
        for s in value['series']:
            if not isinstance(s.get('name'),str) or not s['name'].strip() or len(s['name'])>40 or len(s.get('values',[]))!=spec['pointCount']: raise ValueError('pptx_chart_values_invalid')
            if any(not isinstance(n,(int,float)) or isinstance(n,bool) or not math.isfinite(n) or abs(n)>1e15 for n in s['values']): raise ValueError('pptx_chart_values_invalid')
            if any(k in spec['type'] for k in ('pieChart','doughnutChart')) and (any(n<0 for n in s['values']) or not any(s['values'])): raise ValueError('pptx_chart_values_invalid')

def numeric_categories(labels):
    try:
        result=[float(label.replace(',','.')) for label in labels]
        return result if all(math.isfinite(v) for v in result) else None
    except (ValueError,TypeError,AttributeError):return None

def workbook(data, categories=None):
    # A fresh data-only XLSX: no original formulas, external links, macros, or stale values.
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    sheet=etree.Element('{%s}worksheet'%ns,nsmap={None:ns});rows=etree.SubElement(sheet,'{%s}sheetData'%ns)
    values=[[data.get('title') or 'Category']+[s['name'] for s in data['series']]]+[[label]+[s['values'][i] for s in data['series']] for i,label in enumerate(categories if categories is not None else data['categories'])]
    for i,row in enumerate(values,1):
        r=etree.SubElement(rows,'{%s}row'%ns,r=str(i))
        for j,value in enumerate(row):
            cell=etree.SubElement(r,'{%s}c'%ns,r=chr(65+j)+str(i))
            if isinstance(value,str):
                cell.set('t','inlineStr');etree.SubElement(etree.SubElement(cell,'{%s}is'%ns),'{%s}t'%ns).text=value
            else: etree.SubElement(cell,'{%s}v'%ns).text=str(value)
    parts={
      '[Content_Types].xml':f'<Types xmlns="{NS["ct"]}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
      '_rels/.rels':f'<Relationships xmlns="{NS["pr"]}"><Relationship Id="book" Type="{NS["r"]}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      'xl/workbook.xml':f'<workbook xmlns="{ns}" xmlns:r="{NS["r"]}"><sheets><sheet name="Data" sheetId="1" r:id="sheet"/></sheets></workbook>',
      'xl/_rels/workbook.xml.rels':f'<Relationships xmlns="{NS["pr"]}"><Relationship Id="sheet" Type="{NS["r"]}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
      'xl/worksheets/sheet1.xml':etree.tostring(sheet,xml_declaration=True,encoding='UTF-8')}
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as z:
        for name,value in parts.items(): z.writestr(name,value)
    return result.getvalue()

def normalize_chart(root, palette=(), background=None):
    """Repair supported native charts without rasterizing or changing data."""
    from runtime.contrast import ratio
    def insert_before(parent,child,names):
        parent.insert(next((i for i,n in enumerate(parent) if etree.QName(n).localname in names),len(parent)),child)
    # Mixed-sign horizontal bars cross the category axis: nextTo puts labels
    # directly on the negative bars. Move category labels outside the plot.
    for plot in root.xpath('.//c:plotArea/c:areaChart',namespaces=NS):
        grouping=plot.find('c:grouping',NS)
        series=plot.findall('c:ser',NS)
        if len(series)<2 or grouping is not None and grouping.get('val') in ('stacked','percentStacked'):continue
        for series_node in series:
            # Preserve colors, series order and editable data; only opaque fills
            # acquire a shared translucency. Existing explicit alpha wins.
            for color in series_node.xpath('./c:spPr/a:solidFill/*',namespaces=NS):
                if not color.xpath('./a:alpha|./a:alphaMod|./a:alphaOff',namespaces=NS):
                    etree.SubElement(color,'{%s}alpha'%NS['a'],val='55000')
    for plot in root.xpath('.//c:plotArea/c:barChart[c:barDir[@val="bar"]]',namespaces=NS):
        values=[float(v.text) for v in plot.xpath('./c:ser/c:val//c:pt/c:v',namespaces=NS)]
        if values and min(values)<0<max(values):
            ids={n.get('val') for n in plot.findall('c:axId',NS)}
            for axis in root.xpath('.//c:catAx',namespaces=NS):
                axis_id=axis.find('c:axId',NS)
                if axis_id is None or axis_id.get('val') not in ids:continue
                labels=axis.find('c:tickLblPos',NS)
                if labels is None:
                    labels=etree.Element('{%s}tickLblPos'%NS['c']);insert_before(axis,labels,{'spPr','txPr','crossAx','crosses','crossesAt','auto','lblAlgn','lblOffset','tickLblSkip','tickMarkSkip','noMultiLvlLbl','extLst'})
                labels.set('val','low')
    for plot in root.xpath('.//c:plotArea/c:doughnutChart|.//c:plotArea/c:pieChart|.//c:plotArea/c:barChart',namespaces=NS):
        kind=etree.QName(plot).localname;pie=kind in ('pieChart','doughnutChart')
        if kind=='doughnutChart' and plot.find('c:holeSize',NS) is None:
            insert_before(plot,etree.Element('{%s}holeSize'%NS['c'],val='50'),{'extLst'})
        series=plot.findall('c:ser',NS)
        count=max((len(s.xpath('./c:val//c:pt',namespaces=NS)) for s in series),default=0)
        if pie:
            vary=plot.find('c:varyColors',NS)
            if vary is None:vary=etree.Element('{%s}varyColors'%NS['c']);plot.insert(0,vary)
            vary.set('val','1')
            colors=[c for c in dict.fromkeys(palette) if isinstance(c,str) and len(c)==7 and c.startswith('#') and ratio(c,background or '#FFFFFF')>=2.5]
            for series_node in series:
                # Explicit data-point colors prevent viewers treating all slices
                # as the series' solid fill. Existing per-point styling wins.
                if len(colors)>=count and count>1 and not series_node.findall('c:dPt',NS):
                    for i in range(count):
                        point=etree.Element('{%s}dPt'%NS['c']);etree.SubElement(point,'{%s}idx'%NS['c'],val=str(i))
                        props=etree.SubElement(point,'{%s}spPr'%NS['c']);fill=etree.SubElement(props,'{%s}solidFill'%NS['a'])
                        etree.SubElement(fill,'{%s}srgbClr'%NS['a'],val=colors[i][1:])
                        insert_before(series_node,point,{'dLbls','cat','val','extLst'})
        if not count or count>8 or len(series)>3:continue
        labels=plot.find('c:dLbls',NS)
        if labels is None:
            labels=etree.Element('{%s}dLbls'%NS['c'])
            insert_before(plot,labels,{'firstSliceAng','holeSize','gapWidth','overlap','serLines','axId','extLst'})
        enabled=labels.xpath('./c:showVal[@val="1"]|./c:showCatName[@val="1"]|./c:showPercent[@val="1"]',namespaces=NS)
        if not enabled and not labels.findall('c:dLbl',NS):
            for child in list(labels):
                if etree.QName(child).localname.startswith('show') or etree.QName(child).localname in ('delete','dLblPos'):labels.remove(child)
            # LibreOffice may keep doughnut labels inside even with outEnd.
            # Choose an explicit inside placement and contrasting per-slice
            # text instead; all viewers then use the same design intent.
            insert_before(labels,etree.Element('{%s}dLblPos'%NS['c'],val='ctr' if pie else 'outEnd'),{'separator','showLeaderLines','leaderLines','extLst'})
            category_labels=pie and root.find('c:chart/c:legend',NS) is None
            for name,on in [('showLegendKey',False),('showVal',not pie),('showCatName',category_labels),('showSerName',False),('showPercent',pie),('showBubbleSize',False)]:
                insert_before(labels,etree.Element('{%s}%s'%(NS['c'],name),val='1' if on else '0'),{'separator','showLeaderLines','leaderLines','extLst'})
            if pie and len(series)==1:
                for point in series[0].findall('c:dPt',NS):
                    index=point.find('c:idx',NS);color=point.find('c:spPr/a:solidFill/a:srgbClr',NS)
                    if index is None or color is None:continue
                    slice_background='#'+color.get('val','')
                    if len(slice_background)!=7:continue
                    foreground=max(('#000000','#FFFFFF'),key=lambda c:ratio(c,slice_background))
                    label=etree.Element('{%s}dLbl'%NS['c']);etree.SubElement(label,'{%s}idx'%NS['c'],val=index.get('val'))
                    tx=etree.SubElement(label,'{%s}txPr'%NS['c']);etree.SubElement(tx,'{%s}bodyPr'%NS['a']);etree.SubElement(tx,'{%s}lstStyle'%NS['a'])
                    p=etree.SubElement(tx,'{%s}p'%NS['a']);pp=etree.SubElement(p,'{%s}pPr'%NS['a']);rp=etree.SubElement(pp,'{%s}defRPr'%NS['a'])
                    fill=etree.SubElement(rp,'{%s}solidFill'%NS['a']);etree.SubElement(fill,'{%s}srgbClr'%NS['a'],val=foreground[1:])
                    etree.SubElement(p,'{%s}endParaRPr'%NS['a'])
                    etree.SubElement(label,'{%s}dLblPos'%NS['c'],val='ctr')
                    # Some viewers do not inherit the group's display flags
                    # for a individually styled label. Explicitly repeat them.
                    for name,on in [('showLegendKey',False),('showVal',False),('showCatName',category_labels),('showSerName',False),('showPercent',True),('showBubbleSize',False)]:
                        etree.SubElement(label,'{%s}%s'%(NS['c'],name),val='1' if on else '0')
                    labels.insert(len(labels.findall('c:dLbl',NS)),label)
    from runtime.chart_contrast import repair_chart_contrast
    repair_chart_contrast(root,palette,background)

def fill_chart(spec,value,parts,result,slide_root,slide_rels,slide_index,content_types,palette=(),background=None):
    root=xml(parts[spec['part']]);part=f'ppt/charts/nerpaChart{slide_index+1}_{spec["shapeId"]}.xml'
    numeric=bool(root.xpath('.//c:ser/c:cat/c:numRef|.//c:ser/c:cat/c:numLit',namespaces=NS))
    categories=numeric_categories(value['categories']) if numeric else None
    # Recompute numeric scales for new data; never clip it to the example's min/max.
    for node in root.xpath('.//c:valAx/c:scaling/c:min|.//c:valAx/c:scaling/c:max|.//c:valAx/c:majorUnit|.//c:valAx/c:minorUnit|.//c:dLbl/c:tx',namespaces=NS):node.getparent().remove(node)
    def data_ref(parent,kind,formula,values):
        for node in list(parent): parent.remove(node)
        ref=etree.SubElement(parent,'{%s}%sRef'%(NS['c'],kind));etree.SubElement(ref,'{%s}f'%NS['c']).text=formula
        cache=etree.SubElement(ref,'{%s}%sCache'%(NS['c'],kind))
        if kind=='num': etree.SubElement(cache,'{%s}formatCode'%NS['c']).text='General'
        etree.SubElement(cache,'{%s}ptCount'%NS['c'],val=str(len(values)))
        for i,v in enumerate(values): etree.SubElement(etree.SubElement(cache,'{%s}pt'%NS['c'],idx=str(i)),'{%s}v'%NS['c']).text=str(v)
    for i,s in enumerate(root.xpath('.//c:plotArea/*/c:ser',namespaces=NS)):
        tx=s.find('c:tx',NS)
        if tx is None: tx=etree.Element('{%s}tx'%NS['c']);s.insert(2,tx)
        col=chr(66+i);end=spec['pointCount']+1
        data_ref(tx,'str',f"Data!${col}$1",[value['series'][i]['name']])
        data_ref(s.find('c:cat',NS),'num' if categories is not None else 'str',f'Data!$A$2:$A${end}',categories if categories is not None else value['categories'])
        data_ref(s.find('c:val',NS),'num',f'Data!${col}$2:${col}${end}',value['series'][i]['values'])
    normalize_chart(root,palette,background)
    title=root.find('c:chart/c:title',NS)
    if title is not None:
        tx=title.find('c:tx',NS)
        if tx is not None:
            rich=tx.find('c:rich',NS)
            if rich is not None and rich.xpath('.//a:t',namespaces=NS):
                for i,t in enumerate(rich.xpath('.//a:t',namespaces=NS)): t.text=value.get('title','') if i==0 else ''
            else: data_ref(tx,'str',"Data!$A$1",[value.get('title','')])
    relname=relationship_part(spec['part']);rels=xml(parts[relname]) if relname in parts else etree.Element('{%s}Relationships'%NS['pr'])
    for r in list(rels):
        if r.get('Type','').endswith('/package'): rels.remove(r)
        elif r.get('TargetMode')!='External':
            target=r.get('Target','')
            absolute=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(spec['part']),target))
            r.set('Target',posixpath.relpath(absolute,posixpath.dirname(part)))
    book=f'ppt/embeddings/nerpaChart{slide_index+1}_{spec["shapeId"]}.xlsx'
    rid='nerpaData'
    while any(r.get('Id')==rid for r in rels):rid+='x'
    etree.SubElement(rels,'{%s}Relationship'%NS['pr'],Id=rid,Type=NS['r']+'/package',Target=posixpath.relpath(book,posixpath.dirname(part)))
    external=root.find('c:externalData',NS)
    if external is None: external=etree.SubElement(root,'{%s}externalData'%NS['c'])
    external.set('{%s}id'%NS['r'],rid)
    auto=external.find('c:autoUpdate',NS)
    if auto is None:auto=etree.SubElement(external,'{%s}autoUpdate'%NS['c'])
    auto.set('val','0')
    # Chart style/color/media relationships were rebased to the cloned part.
    result[part]=etree.tostring(root,xml_declaration=True,encoding='UTF-8')
    result[relationship_part(part)]=etree.tostring(rels,xml_declaration=True,encoding='UTF-8')
    result[book]=workbook(value,categories)
    etree.SubElement(content_types,'{%s}Override'%NS['ct'],PartName='/'+part,ContentType='application/vnd.openxmlformats-officedocument.drawingml.chart+xml')
    if not any(n.get('Extension')=='xlsx' for n in content_types):etree.SubElement(content_types,'{%s}Default'%NS['ct'],Extension='xlsx',ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    chart_rid='nerpaChart'+str(spec['shapeId'])
    while any(r.get('Id')==chart_rid for r in slide_rels):chart_rid+='x'
    etree.SubElement(slide_rels,'{%s}Relationship'%NS['pr'],Id=chart_rid,Type=NS['r']+'/chart',Target=posixpath.relpath(part,'ppt/slides'))
    node=slide_root.xpath('.//p:cNvPr[@id=$id]',namespaces=NS,id=str(spec['shapeId']))[0].getparent().getparent()
    node.find('.//c:chart',NS).set('{%s}id'%NS['r'],chart_rid)
