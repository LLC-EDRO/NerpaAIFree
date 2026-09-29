import unittest
from lxml import etree
from runtime.security import NS,xml
from runtime.manual_text import apply_manual_text,set_frame,parent_matrix

class ManualTextTest(unittest.TestCase):
    def source(self,group=False):
        shape='<p:sp><p:nvSpPr><p:cNvPr id="23" name="Original"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="127000" y="254000"/><a:ext cx="2540000" cy="635000"/></a:xfrm></p:spPr><p:txBody><a:bodyPr><a:normAutofit fontScale="75000"/></a:bodyPr><a:p><a:pPr><a:defRPr b="1" sz="2200"/></a:pPr><a:r><a:rPr sz="2200" b="1"><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:rPr><a:t>Заголовок</a:t></a:r><a:endParaRPr sz="2200"/></a:p></p:txBody></p:sp>'
        if group:shape='<p:grpSp><p:nvGrpSpPr/><p:grpSpPr><a:xfrm><a:off x="1270000" y="635000"/><a:ext cx="2540000" cy="1270000"/><a:chOff x="0" y="0"/><a:chExt cx="1270000" cy="635000"/></a:xfrm></p:grpSpPr>'+shape+'</p:grpSp>'
        return xml(f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree>{shape}</p:spTree></p:cSld></p:sld>'.encode())
    def test_source_and_rebuilt_preserve_object_and_override_inherited_styles(self):
        for rebuilt in (False,True):
            root=self.source();key='r_any' if rebuilt else 'any'
            spec=dict(key=key,shapeId=23,x=10,y=20,w=200,h=50)
            native=dict(fields={key:'Заголовок'})
            if rebuilt:native['rebuild']=dict(texts=[spec],tables=[])
            style=dict(x=30,y=40,w=270,h=65,size=30,bold=False,color='#164ac4')
            apply_manual_text(root,dict(slots=[spec]),dict(native=native,textStyles={'native.fields.'+key:style}),dict(textAnchors=[spec]))
            self.assertEqual(root.xpath('.//p:cNvPr/@id',namespaces=NS),['23'])
            self.assertEqual(root.xpath('.//a:t/text()',namespaces=NS),['Заголовок'])
            self.assertEqual(root.find('.//a:off',NS).get('x'),str(30*12700))
            self.assertEqual(root.find('.//a:ext',NS).get('cx'),str(270*12700))
            self.assertIsNone(root.find('.//a:normAutofit',NS))
            for r in root.xpath('.//a:rPr | .//a:defRPr | .//a:endParaRPr',namespaces=NS):
                self.assertEqual(r.get('sz'),'3000');self.assertEqual(r.get('b'),'0');self.assertEqual(r.find('a:solidFill/a:srgbClr',NS).get('val'),'164AC4')
    def test_group_move_converts_page_space_to_local_coordinates(self):
        root=self.source(True);shape=root.find('.//p:sp',NS)
        set_frame(shape,dict(x=140,y=100,w=300,h=80))
        x=shape.find('p:spPr/a:xfrm',NS)
        self.assertEqual(x.find('a:off',NS).get('x'),str(20*12700))
        self.assertEqual(x.find('a:off',NS).get('y'),str(25*12700))
        self.assertEqual(x.find('a:ext',NS).get('cx'),str(150*12700))
        self.assertEqual(root.find('.//p:grpSp',NS).tag,'{%s}grpSp'%NS['p'])
    def test_table_formatting_preserves_rows_and_rejects_cell_movement(self):
        root=self.source();tree=root.find('p:cSld/p:spTree',NS)
        frame=etree.SubElement(tree,'{%s}graphicFrame'%NS['p']);nv=etree.SubElement(frame,'{%s}nvGraphicFramePr'%NS['p']);etree.SubElement(nv,'{%s}cNvPr'%NS['p'],id='44')
        table=etree.SubElement(frame,'{%s}tbl'%NS['a']);row=etree.SubElement(table,'{%s}tr'%NS['a']);cell=etree.SubElement(row,'{%s}tc'%NS['a'])
        body=etree.SubElement(cell,'{%s}txBody'%NS['a']);etree.SubElement(body,'{%s}bodyPr'%NS['a']);p=etree.SubElement(body,'{%s}p'%NS['a']);run=etree.SubElement(p,'{%s}r'%NS['a']);etree.SubElement(run,'{%s}t'%NS['a']).text='42'
        layout=dict(slots=[dict(key='cell',shapeId=44,cell=[0,0])]);slide=dict(native=dict(fields=dict(cell='42')),textStyles={'native.fields.cell':dict(size=16,bold=True)})
        apply_manual_text(root,layout,slide)
        self.assertEqual(run.find('a:rPr',NS).get('sz'),'1600');self.assertEqual(len(table.findall('a:tr',NS)),1)
        slide['textStyles']['native.fields.cell']['x']=20
        with self.assertRaisesRegex(ValueError,'cell_geometry'):apply_manual_text(root,layout,slide)

if __name__=='__main__':unittest.main()
