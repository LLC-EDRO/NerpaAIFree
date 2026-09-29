"""Repair native chart ink against a known background, without an AI round trip."""
import re
from collections import Counter
from lxml import etree
from runtime.security import NS
from runtime.contrast import ratio

def solid_color(parent,path):
    color=parent.find(path+'/a:solidFill/a:srgbClr',NS)
    if color is None or color.find('a:alpha',NS) is not None:return None
    value=color.get('val','')
    return '#'+value if re.fullmatch(r'[0-9a-fA-F]{6}',value) else None

def source_background(pdf_path,index,spec):
    """Require agreement across the chart's border pixels; never guess a photo."""
    if not pdf_path.exists():return None
    import pymupdf
    with pymupdf.open(pdf_path) as pdf:
        if not 0<=index<len(pdf):return None
        page=pdf[index];clip=pymupdf.Rect(spec['x'],spec['y'],spec['x']+spec['w'],spec['y']+spec['h'])&page.rect
        if clip.is_empty:return None
        pix=page.get_pixmap(clip=clip,matrix=pymupdf.Matrix(.5,.5),colorspace=pymupdf.csRGB,alpha=False)
        data=pix.samples;colors=Counter();total=0
        for y in range(pix.height):
            for x in range(pix.width):
                if min(x,pix.width-1-x)>=max(1,pix.width*.06) and min(y,pix.height-1-y)>=max(1,pix.height*.06):continue
                i=(y*pix.width+x)*3;colors[tuple(data[i:i+3])]+=1;total+=1
        if not colors:return None
        color,count=colors.most_common(1)[0]
        return '#%02X%02X%02X'%color if count/total>=.7 else None

def insert(parent,child,before):
    parent.insert(next((i for i,n in enumerate(parent) if etree.QName(n).localname in before),len(parent)),child)

def text_contrast(parent,background,palette):
    tx=parent.find('c:txPr',NS)
    if tx is None:
        tx=etree.Element('{%s}txPr'%NS['c'])
        insert(parent,tx,{'dLblPos','showLegendKey','showVal','showCatName','showSerName','showPercent','showBubbleSize','crossAx','externalData','printSettings','extLst'})
        etree.SubElement(tx,'{%s}bodyPr'%NS['a']);etree.SubElement(tx,'{%s}lstStyle'%NS['a'])
        p=etree.SubElement(tx,'{%s}p'%NS['a']);pp=etree.SubElement(p,'{%s}pPr'%NS['a']);etree.SubElement(pp,'{%s}defRPr'%NS['a'])
    props=tx.xpath('.//a:defRPr|.//a:rPr|.//a:endParaRPr',namespaces=NS)
    for p in props:
        color=solid_color(p,'.')
        # Unknown custom theme colors are retained; missing chart ink is black.
        if color is None and p.find('a:solidFill',NS) is not None:continue
        # Inherited chart ink may have been changed at chartSpace level. Give
        # inside labels an explicit contrasting color instead of assuming black.
        if color and ratio(color,background)>=4.5:continue
        options=[c for c in palette if re.fullmatch(r'#[0-9a-fA-F]{6}',c or '') and ratio(c,background)>=4.5]
        foreground=max(options or ['#000000','#FFFFFF'],key=lambda c:ratio(c,background))
        for fill in p.xpath('./a:solidFill|./a:gradFill|./a:noFill',namespaces=NS):p.remove(fill)
        fill=etree.Element('{%s}solidFill'%NS['a']);etree.SubElement(fill,'{%s}srgbClr'%NS['a'],val=foreground[1:])
        insert(p,fill,{'effectLst','effectDag','highlight','uLnTx','uLn','uFillTx','uFill','latin','ea','cs','sym','hlinkClick','hlinkMouseOver','rtl','extLst'})

def repair_chart_contrast(root,palette,background):
    background=solid_color(root,'c:spPr') or background
    if not background:return
    plot=root.find('c:chart/c:plotArea',NS)
    plot_background=solid_color(plot,'c:spPr') if plot is not None else None
    plot_background=plot_background or background
    text_contrast(root,background,palette)
    for parent in root.xpath('.//c:legend|.//c:catAx|.//c:valAx',namespaces=NS):text_contrast(parent,background,palette)
    for kind in root.xpath('.//c:plotArea/*[c:ser]',namespaces=NS):
        pie=etree.QName(kind).localname in ('pieChart','doughnutChart')
        for labels in kind.xpath('./c:dLbls|./c:ser/c:dLbls',namespaces=NS):
            pos=labels.find('c:dLblPos',NS)
            outside=pos is not None and pos.get('val') in ('outEnd','bestFit')
            if not pie and (outside or pos is None):text_contrast(labels,plot_background,palette)
            if not pie:continue
            colors=[c for c in dict.fromkeys(palette) if re.fullmatch(r'#[0-9a-fA-F]{6}',c or '') and ratio(c,plot_background)>=2.5]
            for series in kind.findall('c:ser',NS):
                used={solid_color(p,'c:spPr') for p in series.findall('c:dPt',NS)}
                for point in series.findall('c:dPt',NS):
                    color=solid_color(point,'c:spPr');idx=point.find('c:idx',NS)
                    if not color or idx is None:continue
                    if ratio(color,plot_background)<1.7:
                        replacement=next((c for c in colors if c not in used),None)
                        if replacement:
                            point.find('c:spPr/a:solidFill/a:srgbClr',NS).set('val',replacement[1:]);used.add(replacement);color=replacement
                    individual=next((n for n in labels.findall('c:dLbl',NS) if n.find('c:idx',NS) is not None and n.find('c:idx',NS).get('val')==idx.get('val')),None)
                    if individual is None:
                        individual=etree.Element('{%s}dLbl'%NS['c']);etree.SubElement(individual,'{%s}idx'%NS['c'],val=idx.get('val'));labels.insert(len(labels.findall('c:dLbl',NS)),individual)
                        from copy import deepcopy
                        for flag in labels:
                            if etree.QName(flag).localname.startswith('show') or etree.QName(flag).localname=='dLblPos':individual.append(deepcopy(flag))
                    individual_pos=individual.find('c:dLblPos',NS)
                    on_background=outside if individual_pos is None else individual_pos.get('val')=='outEnd'
                    text_contrast(individual,plot_background if on_background else color,palette)
