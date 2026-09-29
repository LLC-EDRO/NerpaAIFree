import unittest
from copy import deepcopy
from lxml import etree
from runtime.security import NS,xml
from runtime.vector_charts import candidates,validate_plans,apply_plans

class VectorCharts(unittest.TestCase):
    def fixture(self):
        sectors=''.join(f'''<p:sp><p:nvSpPr><p:cNvPr id="{identity}" name="Sector"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1000000" cy="1000000"/></a:xfrm><a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/><a:rect l="0" t="0" r="r" b="b"/><a:pathLst><a:path w="1000000" h="1000000"><a:moveTo><a:pt x="0" y="0"/></a:moveTo><a:lnTo><a:pt x="100" y="0"/></a:lnTo><a:cubicBezTo><a:pt x="100" y="50"/><a:pt x="50" y="100"/><a:pt x="0" y="0"/></a:cubicBezTo><a:close/></a:path></a:pathLst></a:custGeom><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></p:spPr></p:sp>''' for identity,color in [(91,'AA3311'),(74,'3366AA')])
        root=xml(f'''<p:sld xmlns:p="{NS['p']}" xmlns:a="{NS['a']}"><p:cSld><p:spTree><p:grpSp><p:nvGrpSpPr><p:cNvPr id="60" name="Plot"/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1000000" cy="1000000"/><a:chOff x="0" y="0"/><a:chExt cx="1000000" cy="1000000"/></a:xfrm></p:grpSpPr>{sectors}</p:grpSp></p:spTree></p:cSld></p:sld>'''.encode())
        slots=[dict(key='alpha',text='A 30%'),dict(key='beta',text='B 70%')]
        p=dict(vectorCharts=candidates(root,slots),textAnchors=[dict(key='r_a',sourceKey='alpha'),dict(key='r_b',sourceKey='beta')])
        scene=dict(texts=[dict(key=k,x=120,y=i*40,size=20) for i,k in enumerate(['r_a','r_b'])],graphicCharts=[dict(groupId=60,kind='donut',holeRatio=.5,bindings=[dict(shapeId=91,fieldKey='r_a'),dict(shapeId=74,fieldKey='r_b')])])
        return root,slots,p,dict(rebuild=scene,fields=dict(r_a='North 80%',r_b='South 20%'))

    def test_new_values_change_editable_sector_areas_and_keep_colours(self):
        root,slots,p,n=self.fixture();self.assertEqual(len(p['vectorCharts']),1)
        validate_plans(p,n['rebuild'],n['fields']);apply_plans(root,p,n)
        areas=[]
        for identity,color in [(91,'AA3311'),(74,'3366AA')]:
            shape=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(identity))[0]
            self.assertEqual(shape.find('p:spPr/a:solidFill/a:srgbClr',NS).get('val'),color)
            points=[(int(v.get('x')),int(v.get('y'))) for v in shape.findall('.//a:path//a:pt',NS)]
            areas.append(abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1])))/2)
        self.assertAlmostEqual(areas[0]/sum(areas),.8,places=3)

    def test_ambiguous_values_missing_binding_and_non_percentage_art_are_not_rewritten(self):
        root,slots,p,n=self.fixture()
        self.assertEqual(candidates(root,[dict(key='a',text='Team'),dict(key='b',text='Growth')]),[])
        for mutate in [lambda v:v['rebuild'].update(graphicCharts=[]),lambda v:v['fields'].update(r_a='80% and 15%'),lambda v:v['fields'].update(r_b='30%'),lambda v:v['rebuild']['graphicCharts'][0]['bindings'][0].update(shapeId=999)]:
            v=deepcopy(n);mutate(v)
            with self.assertRaises(ValueError):validate_plans(p,v['rebuild'],v['fields'])

if __name__=='__main__':unittest.main()
