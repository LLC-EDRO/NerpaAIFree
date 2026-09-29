import unittest,io,zipfile
from types import SimpleNamespace
from copy import deepcopy
from lxml import etree
from runtime.security import NS,xml
from runtime.charts import chart_spec,fill_chart
from runtime import test_lab_expansion as fixtures
from runtime.geometry_repair import apply_repairs,repair_context
from runtime.safe_text_expansion import apply_expansion
from runtime.text_containers import normalize_frame_bounds,container_for
from runtime.text_protection import protected_furniture

class GeneralRepairTest(unittest.TestCase):
    def test_native_numeric_categories_remain_editable_or_switch_to_labels(self):
        for kind in ('lineChart','areaChart'):
            source=xml(f'''<c:chartSpace xmlns:c="{NS['c']}" xmlns:r="{NS['r']}"><c:chart><c:plotArea><c:{kind}><c:ser><c:idx val="0"/><c:order val="0"/><c:cat><c:numRef><c:f>Data!$A$2:$A$3</c:f><c:numCache><c:ptCount val="2"/><c:pt idx="0"><c:v>1</c:v></c:pt><c:pt idx="1"><c:v>2</c:v></c:pt></c:numCache></c:numRef></c:cat><c:val><c:numLit><c:ptCount val="2"/><c:pt idx="0"><c:v>10</c:v></c:pt><c:pt idx="1"><c:v>20</c:v></c:pt></c:numLit></c:val></c:ser></c:{kind}></c:plotArea></c:chart></c:chartSpace>'''.encode())
            parts={'ppt/charts/chart1.xml':etree.tostring(source)}
            spec=chart_spec(SimpleNamespace(media_id='ppt/charts/chart1.xml',shape_id=7),parts)
            self.assertEqual(spec['pointCount'],2)
            for categories,numeric in [(['3','4'],True),(['Север','Юг'],False)]:
                slide=xml(f'<p:sld xmlns:p="{NS["p"]}" xmlns:c="{NS["c"]}" xmlns:r="{NS["r"]}"><p:cSld><p:spTree><p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="7"/></p:nvGraphicFramePr><c:chart r:id="old"/></p:graphicFrame></p:spTree></p:cSld></p:sld>'.encode())
                rels=xml(f'<Relationships xmlns="{NS["pr"]}"/>'.encode());types=xml(f'<Types xmlns="{NS["ct"]}"/>'.encode());result={}
                fill_chart(spec,dict(title='Test',categories=categories,series=[dict(name='Series',values=[-3,5])]),parts,result,slide,rels,0,types)
                chart=xml(next(v for k,v in result.items() if k.endswith('.xml') and '/charts/' in k))
                self.assertEqual(bool(chart.xpath('.//c:cat/c:numRef',namespaces=NS)),numeric)
                self.assertEqual(chart.xpath('.//c:val//c:pt/c:v/text()',namespaces=NS),['-3','5'])
                book=next(v for k,v in result.items() if k.endswith('.xlsx'))
                with zipfile.ZipFile(io.BytesIO(book)) as z:sheet=xml(z.read('xl/worksheets/sheet1.xml'))
                cell=sheet.xpath('//*[local-name()="c"][@r="A2"]')[0]
                self.assertEqual(cell.get('t') is None,numeric)
    def test_grouped_frame_uses_local_scale_without_destroying_group(self):
        layout,frames,context=fixtures.ExpansionTest().scene();root=context.roots['layout'];tree=root.find('p:cSld/p:spTree',NS);shape=tree[0];tree.remove(shape)
        group=etree.SubElement(tree,'{%s}grpSp'%NS['p']);props=etree.SubElement(group,'{%s}grpSpPr'%NS['p']);xf=etree.SubElement(props,'{%s}xfrm'%NS['a']);etree.SubElement(xf,'{%s}off'%NS['a'],x='0',y='0');etree.SubElement(xf,'{%s}ext'%NS['a'],cx='200',cy='200');etree.SubElement(xf,'{%s}chOff'%NS['a'],x='0',y='0');etree.SubElement(xf,'{%s}chExt'%NS['a'],cx='100',cy='100');group.append(shape)
        for node,attrs in [(shape.find('p:spPr/a:xfrm/a:off',NS),('x','y')),(shape.find('p:spPr/a:xfrm/a:ext',NS),('cx','cy'))]:
            for attr in attrs:node.set(attr,str(int(node.get(attr))//2))
        frames['heading']['width_emu']//=2;frames['heading']['height_emu']//=2
        old_group=etree.tostring(props)
        fitted,changes,issues=apply_repairs(layout,frames,{'heading':dict(x=24,y=20,w=90,h=40)},context)
        self.assertFalse(issues);self.assertEqual(fitted['heading']['width_emu'],90*12700//2)
        apply_expansion(root,changes)
        self.assertEqual(shape.find('p:spPr/a:xfrm/a:off',NS).get('x'),str(24*12700//2))
        self.assertEqual(etree.tostring(props),old_group)
        xf.set('rot','60000');self.assertEqual(repair_context(layout,context,{'heading'})['fields'],[])
    def test_overlong_caption_is_clamped_to_its_card_not_a_picture(self):
        layout,frames,context=fixtures.ExpansionTest().scene();root=context.roots['layout'];tree=root.find('p:cSld/p:spTree',NS)
        card=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:nvSpPr><p:cNvPr id="4"/></p:nvSpPr><p:spPr><a:prstGeom prst="rect"/><a:solidFill><a:srgbClr val="AA0000"/></a:solidFill></p:spPr></p:sp>'.encode());tree.insert(0,card)
        context.slides['layout'].insert(0,fixtures.ExpansionTest().item(4,10,10,60,70))
        bounded,changes=normalize_frame_bounds(layout,frames,context)
        self.assertEqual(changes['heading']['w'],50);self.assertEqual(bounded['heading']['width_emu'],50*12700)
        self.assertTrue(apply_repairs(layout,frames,{'heading':dict(x=20,y=20,w=70,h=40)},context)[2])
        context.slides['layout'][0]['object_kind']='picture';self.assertIsNone(container_for(layout['slots'][0],context.slides['layout'],root))
        context.slides['layout'][0]['object_kind']='shape';etree.SubElement(card.find('p:spPr',NS),'{%s}noFill'%NS['a']);self.assertIsNone(container_for(layout['slots'][0],context.slides['layout'],root))
    def test_bottom_position_does_not_protect_a_caption(self):
        self.assertFalse(protected_furniture(dict(role='footer',confidence=dict(score=.62)),{}))
        self.assertFalse(protected_furniture(dict(role='footer',confidence=dict(score=.62)),dict(placeholder_type='ftr')))
        self.assertFalse(protected_furniture(dict(role='footer',recurring_pattern_id='recurring'),{}))
        self.assertTrue(protected_furniture(dict(role='logo'),{}))
    def test_ai_centering_uses_single_label_card_but_not_a_shared_metric_block(self):
        layout,frames,context=fixtures.ExpansionTest().scene();root=context.roots['layout'];tree=root.find('p:cSld/p:spTree',NS)
        card=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:nvSpPr><p:cNvPr id="4"/></p:nvSpPr><p:spPr><a:prstGeom prst="rect"/><a:solidFill><a:srgbClr val="AA0000"/></a:solidFill></p:spPr></p:sp>'.encode());tree.insert(0,card);context.slides['layout'].insert(0,fixtures.ExpansionTest().item(4,10,10,100,120))
        choice={'heading':dict(vertical='center',reason='One short label in a card')}
        _,changes=normalize_frame_bounds(layout,frames,context,choice,{'heading':'Motor'})
        self.assertEqual(changes['heading']['y'],16);self.assertEqual(changes['heading']['h'],108)
        layout['slots'].append(dict(layout['slots'][0],key='caption',shapeId=92,y=80,h=20))
        self.assertFalse(normalize_frame_bounds(layout,frames,context,choice,{'heading':'Motor'})[1])
