import unittest
from copy import deepcopy
from types import SimpleNamespace
from runtime.rebuild import validate,apply,fields
from runtime.security import NS,xml
from app.presentation.parser.agent import PresentationParser

class RebuildTest(unittest.TestCase):
    def test_straightened_text_uses_target_frame_not_rotated_source_bounds(self):
        root,p,n,_,_=self.source_scene()
        shape=root.find('.//p:sp',NS);x=shape.find('p:spPr/a:xfrm',NS)
        x.set('rot',str(270*60000))
        p['textAnchors'][0].update(x=95,y=-40,w=20,h=150,rotation=270,repairRegion=dict(x=0,y=0,w=720,h=405))
        n['rebuild']['texts'][0].update(x=80,y=30,w=120,h=24,rotation=0)
        apply(root,p,n)
        self.assertEqual(x.get('rot'),'0')
        self.assertEqual([int(x.find(a,NS).get(attr))/12700 for a,attr in [('a:off','x'),('a:off','y'),('a:ext','cx'),('a:ext','cy')]],[80,30,120,24])

    def test_straightened_grouped_text_inverts_parent_scaling(self):
        from runtime.rebuild import straightened_frame
        from runtime.legacy.composition import node
        root,p,n,_,group=self.source_scene()
        shape=root.find('.//p:sp',NS);shape.getparent().remove(shape);group.append(shape)
        x=node(group.find('p:grpSpPr',NS),'a:xfrm')
        for name,attrs in [('off',dict(x=100*12700,y=50*12700)),('ext',dict(cx=400*12700,cy=300*12700)),('chOff',dict(x=10*12700,y=20*12700)),('chExt',dict(cx=200*12700,cy=100*12700))]:node(x,'a:'+name,**attrs)
        self.assertEqual(straightened_frame(shape,dict(x=140,y=80,w=120,h=60)),dict(x=30,y=30,w=60,h=20))

    def test_source_table_merges_cannot_hide_new_independent_headers(self):
        from runtime.rebuild import add_table
        root,p,n,_,_=self.source_scene();_,data=self.scene()
        table=data['rebuild']['tables'][0]
        add_table(root.find('p:cSld/p:spTree',NS),40,table,data['fields'])
        cells=root.findall('.//a:tr/a:tc',NS)
        cells[0].set('gridSpan','2');cells[1].set('hMerge','1')
        cells[2].set('rowSpan','2');cells[4].set('vMerge','1')
        p['tables']=[dict(shapeId=40,columns=2,box=dict(x=30,y=95,w=660,h=210))]
        n['rebuild']['tables']=[table];n['fields'].update(data['fields'])
        apply(root,p,n)
        self.assertTrue(all(not set(c.attrib)&{'gridSpan','rowSpan','hMerge','vMerge'} for c in cells))
        self.assertEqual([c.find('.//a:t',NS).text for c in cells],['Район','Состояние','Северный','Проверен','Южный','В работе'])

    def test_native_table_fields_retain_distinct_cell_styles(self):
        scene=dict(texts=[],tables=[dict(x=0,y=0,w=200,h=80,font='Arial',size=14,color='#111111',rows=[['h'],['b']],cellStyles={'h':dict(color='#FFFFFF',bold=True,align='center'),'b':dict(color='#111111',bold=False)})])
        result=fields(scene)
        self.assertEqual(result[0]['color'],'#FFFFFF')
        self.assertEqual(result[0]['align'],'center')
        self.assertEqual(result[1]['color'],'#111111')
        self.assertFalse(result[1]['bold'])

    def test_overlap_region_is_widened_only_for_independent_colliding_text(self):
        from runtime.rebuild import collision_repair_region
        page=dict(x=0,y=0,w=960,h=540)
        a=dict(key='a',x=100,y=100,w=100,h=60,repairRegion=dict(x=90,y=90,w=120,h=80))
        b=dict(key='b',x=150,y=110,w=120,h=40)
        original=deepcopy(a)
        collision_repair_region(a,[a,b],page)
        self.assertEqual(a['repairRegion'],page)
        self.assertEqual(a['sourceOverlapPeers'],['b'])
        a=deepcopy(original);a['container']=dict(x=80,y=80,w=220,h=160)
        collision_repair_region(a,[a,b],page)
        self.assertEqual(a['repairRegion'],original['repairRegion'])
        a=deepcopy(original);b['x']=400
        collision_repair_region(a,[a,b],page)
        self.assertEqual(a,original)

    def test_blank_design_only_clears_editable_text_and_retains_shapes(self):
        from runtime.repair_context import clear_editable_text
        from runtime.legacy.composition import add_text
        from lxml import etree
        root,p,n,dot,group=self.source_scene()
        add_text(root.find('p:cSld/p:spTree',NS),100,{**p['textAnchors'][0],'x':400},'Protected brand')
        before=etree.tostring(group)
        clear_editable_text(root,p)
        self.assertEqual(root.find('.//a:t',NS).text,'')
        self.assertIn('Protected brand',[t.text for t in root.findall('.//a:t',NS)])
        self.assertEqual(etree.tostring(group),before)

    def test_recovery_region_allows_relocation_and_readable_font_reduction(self):
        root,p,n,_,_=self.source_scene()
        anchor=p['textAnchors'][0]
        anchor.update(size=60,minSize=12,repairRegion=dict(x=20,y=15,w=220,h=120))
        spec=n['rebuild']['texts'][0];spec.update(y=65,size=18,h=28)
        self.assertEqual(validate(p,n,0),[])
        spec.update(y=250)
        with self.assertRaises(ValueError):validate(p,n,0)

    def test_new_profiles_report_bad_geometry_with_field_and_region(self):
        root,p,n,_,_=self.source_scene();p['version']=6
        p['textAnchors'][0]['repairRegion']=dict(x=20,y=20,w=200,h=100)
        n['rebuild']['texts'][0].update(x=700,y=400)
        issues=validate(p,n,0)
        self.assertTrue(any(i['key']=='r_title' and i.get('allowedRegion')==p['page'] for i in issues))
        self.assertTrue(any('rebuild_source_anchor_lost' in i['details'] for i in issues))

    def test_underlay_waiver_requires_source_order_evidence(self):
        root,p,n,_,_=self.source_scene();p['version']=6
        p['textAnchors'][0]['underlayCandidates']=[55]
        p['decorations'][0]['outlineOnly']=True
        n['rebuild']['texts'][0].update(h=160,allowUnderlayShapes=[55])
        self.assertFalse(any('rebuild_design_overlap' in i['details'] for i in validate(p,n,0)))
        n['rebuild']['texts'][0]['allowUnderlayShapes']=[999]
        with self.assertRaises(ValueError):validate(p,n,0)

    def test_bad_member_does_not_abort_native_batch(self):
        from unittest.mock import patch
        from io import StringIO
        import bridge,json
        def check(folder,slides,*args):
            if any(s.get('bad') for s in slides):raise ValueError('pptx_fields_mismatch')
            return [dict(slide=0,key='field',reason='overflow')]
        request=dict(action='fit',folder='/work/template',slides=[{},dict(bad=True)])
        with patch('bridge.validate_fields',side_effect=check),patch('sys.stdin',StringIO(json.dumps(request))):
            result=bridge.main()
        self.assertEqual(result['slideErrors'],[dict(slide=1,code='pptx_fields_mismatch')])
        self.assertEqual(result['issues'][0]['slide'],0)
    def scene(self):
        profile=dict(fingerprint='test',fonts=['Liberation Sans'],colors=['#163248'],page=dict(x=0,y=0,w=720,h=405),protectedBoxes=[dict(x=0,y=380,w=720,h=25)],keepShapeIds=[2],pictures=[],charts=[],tables=[dict(shapeId=40,columns=2)])
        title=dict(key='r_title',x=30,y=20,w=660,h=48,font='Liberation Sans',size=28,color='#163248',bold=True,align='left')
        table=dict(shapeId=40,x=30,y=95,w=660,h=210,font='Liberation Sans',size=18,color='#163248',rows=[['r_h1','r_h2'],['r_a','r_b'],['r_c','r_d']])
        native=dict(rebuild=dict(version=1,fingerprint='test',texts=[title],tables=[table],pictures=[],charts=[]),fields=dict(r_title='Результаты',r_h1='Район',r_h2='Состояние',r_a='Северный',r_b='Проверен',r_c='Южный',r_d='В работе'))
        return profile,native
    def test_native_cells_remain_separate_rows_and_footer_is_kept(self):
        p,n=self.scene();self.assertEqual(validate(p,n,0),[])
        root=xml(f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Footer"/></p:nvSpPr></p:sp><p:sp><p:nvSpPr><p:cNvPr id="10" name="Broken"/></p:nvSpPr></p:sp></p:spTree></p:cSld></p:sld>'.encode())
        apply(root,p,n)
        self.assertIsNotNone(root.find('p:cSld/p:spTree/p:nvGrpSpPr',NS))
        self.assertEqual(len(root.findall('.//a:tbl/a:tr',NS)),3)
        self.assertTrue(all(len(r.findall('a:tc',NS))==2 for r in root.findall('.//a:tbl/a:tr',NS)))
        self.assertEqual(len(root.findall('.//p:cNvPr[@name="Footer"]',NS)),1)
        self.assertEqual(root.findall('.//p:cNvPr[@name="Broken"]',NS),[])
        self.assertEqual(len(fields(n['rebuild'])),7)
    def test_collisions_style_missing_objects_and_unbounded_values_fail(self):
        mutations=[lambda n:n['rebuild']['texts'][0].update(y=390),lambda n:n['rebuild']['texts'][0].update(y=110),lambda n:n['rebuild']['texts'][0].update(font='Unexpected'),lambda n:n['rebuild'].update(tables=[]),lambda n:n['rebuild']['texts'][0].update(x=float('nan')),lambda n:n['rebuild']['tables'][0].update(rows=[['r_h1'],['r_a']])]
        for mutate in mutations:
            p,n=self.scene();mutate(n)
            with self.assertRaises(ValueError):validate(p,n,0)
    def test_chart_part_uses_content_types_not_directory_name(self):
        charts=[SimpleNamespace(source_part='ppt/slides/charts/figure.xml')]
        valid=PresentationParser._qa([],[],[],charts,[],[],['ppt/slides/charts/figure.xml'])
        self.assertTrue(valid.passed)
        self.assertEqual(PresentationParser._qa([],[],[],charts,[],[],[]).native_charts_without_chart_part,1)

    def source_scene(self):
        from lxml import etree
        from runtime.legacy.composition import node,add_text,add_shape
        root=etree.Element('{%s}sld'%NS['p'],nsmap={'p':NS['p'],'a':NS['a']})
        tree=node(node(root,'p:cSld'),'p:spTree')
        node(tree,'p:nvGrpSpPr');node(tree,'p:grpSpPr')
        title=dict(key='r_title',sourceKey='s91',shapeId=91,x=30,y=25,w=150,h=20,font='Liberation Sans',size=12,color='#163248',bold=False,align='left')
        add_text(tree,91,title,'Original')
        dot=add_shape(tree,55,'milestone',dict(x=25,y=150,w=10,h=10),'#163248')
        dot.find('p:spPr/a:prstGeom',NS).set('prst','ellipse')
        group=node(tree,'p:grpSp');nv=node(group,'p:nvGrpSpPr');node(nv,'p:cNvPr',id=80,name='Grouped ornament');node(group,'p:grpSpPr')
        add_shape(group,81,'ornament',dict(x=30,y=190,w=20,h=5),'#163248')
        profile=dict(version=2,fingerprint='source',fonts=['Liberation Sans'],colors=['#163248'],page=dict(x=0,y=0,w=720,h=405),protectedBoxes=[],keepShapeIds=[],pictures=[],charts=[],tables=[],textAnchors=[title],decorations=[dict(shapeId=55,box=dict(x=25,y=150,w=10,h=10))])
        spec={k:v for k,v in title.items() if k not in ('sourceKey','shapeId')};spec.update(w=190,h=30)
        native=dict(rebuild=dict(version=1,fingerprint='source',texts=[spec],pictures=[],charts=[],tables=[]),fields=dict(r_title='A new heading'))
        return root,profile,native,dot,group

    def test_source_repair_keeps_native_decorations_groups_ids_and_drawing_order(self):
        from lxml import etree
        root,p,n,dot,group=self.source_scene();before=[etree.tostring(dot),etree.tostring(group)]
        self.assertEqual(validate(p,n,0),[]);apply(root,p,n)
        self.assertEqual([etree.tostring(dot),etree.tostring(group)],before)
        self.assertEqual([int(x.get('id')) for x in root.findall('.//p:cNvPr',NS)],[91,55,80,81])
        self.assertEqual(root.find('.//a:t',NS).text,'A new heading')
        self.assertEqual(root.find('.//p:sp/p:spPr/a:xfrm/a:ext',NS).get('cx'),str(190*12700))

    def test_source_repair_rejects_missing_fields_design_drift_and_decor_collision(self):
        for mutate in [lambda n:n['rebuild'].update(texts=[]),lambda n:n['rebuild']['texts'][0].update(y=250),lambda n:n['rebuild']['texts'][0].update(size=24)]:
            root,p,n,_,_=self.source_scene();mutate(n)
            with self.assertRaises(ValueError):validate(p,n,0)

    def test_decoration_collision_returns_actionable_issue_instead_of_aborting(self):
        root,p,n,_,_=self.source_scene();n['rebuild']['texts'][0]['h']=160
        issues=validate(p,n,0)
        collision=next(i for i in issues if 'rebuild_design_overlap' in i['details'])
        self.assertEqual(collision['key'],'r_title')
        self.assertEqual(collision['blockerShapeId'],55)

    def test_source_card_underlay_is_allowed_but_new_overlap_is_not(self):
        root,p,n,_,_=self.source_scene()
        card=dict(shapeId=200,box=dict(x=20,y=20,w=210,h=100))
        p['pictures']=[card];n['rebuild']['pictures']=[dict(shapeId=200,**card['box'])]
        self.assertEqual(validate(p,n,0),[])
        n['rebuild']['texts'][0].update(w=230)
        self.assertTrue(any('rebuild_overlap' in i['details'] for i in validate(p,n,0)))

    def test_empty_native_text_body_is_decoration_not_content(self):
        from runtime.rebuild import has_native_text
        shape=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:txBody><a:p><a:r><a:t/></a:r></a:p></p:txBody></p:sp>'.encode())
        self.assertFalse(has_native_text(shape))
        shape.find('.//a:t',NS).text=' ' ;self.assertFalse(has_native_text(shape))
        shape.find('.//a:t',NS).text='Label';self.assertTrue(has_native_text(shape))

    def test_empty_transparent_helper_frame_does_not_reserve_space(self):
        from runtime.rebuild import visible_decoration
        shape=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:spPr><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:p><a:r><a:t/></a:r></a:p></p:txBody></p:sp>'.encode())
        self.assertFalse(visible_decoration(shape))
        fill=shape.find('p:spPr/a:noFill',NS);fill.tag='{%s}solidFill'%NS['a']
        self.assertTrue(visible_decoration(shape))

    def test_explicit_palette_contrast_repair_keeps_every_other_source_style(self):
        root,p,n,_,_=self.source_scene();p['colors'].append('#000000')
        n['rebuild']['texts'][0]['color']='#000000'
        with self.assertRaises(ValueError):validate(p,n,0)
        n['rebuild']['colorRepairs']={'r_title':'#000000'}
        self.assertEqual(validate(p,n,0),[])
        apply(root,p,n)
        self.assertEqual(root.find('.//a:rPr/a:solidFill/a:srgbClr',NS).get('val'),'000000')
        n['rebuild']['texts'][0]['font']='Other'
        with self.assertRaises(ValueError):validate(p,n,0)
