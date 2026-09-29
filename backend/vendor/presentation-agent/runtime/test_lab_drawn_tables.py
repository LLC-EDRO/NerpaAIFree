import unittest
from copy import deepcopy
from lxml import etree
from runtime.security import NS,xml
from runtime.drawn_tables import recover_drawn_tables


class DrawnTables(unittest.TestCase):
    def fixture(self,rows=6,cols=4,rules=True):
        shapes=[];identity=50
        for r in range(rows):
            for c in range(cols):
                identity+=1
                shapes.append(f'<p:sp><p:nvSpPr><p:cNvPr id="{identity}" name="cell"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{(20+c*100)*12700}" y="{(50+r*30)*12700}"/><a:ext cx="{80*12700}" cy="{15*12700}"/></a:xfrm><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="1200"><a:latin typeface="DejaVu Sans"/></a:rPr><a:t>Entry {r} {c}</a:t></a:r></a:p></p:txBody></p:sp>')
            if rules:
                identity+=1
                shapes.append(f'<p:sp><p:nvSpPr><p:cNvPr id="{identity}" name="rule"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{20*12700}" y="{(72+r*30)*12700}"/><a:ext cx="{cols*100*12700}" cy="6350"/></a:xfrm><a:solidFill><a:srgbClr val="DDDDDD"/></a:solidFill></p:spPr></p:sp>')
        return {'ppt/slides/slide9.xml':f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>{"".join(shapes)}</p:spTree></p:cSld></p:sld>'.encode()}

    def test_complete_grid_becomes_native_table_preserving_values_and_fonts(self):
        original=self.fixture();before=deepcopy(original)
        result,changes=recover_drawn_tables(original)
        root=xml(next(iter(result.values())));table=root.find('.//a:tbl',NS)
        self.assertEqual(original,before)
        self.assertEqual(len(changes),1)
        self.assertEqual(len(table.findall('a:tr',NS)),6)
        self.assertEqual(len(table.findall('a:tblGrid/a:gridCol',NS)),4)
        self.assertEqual([n.text for n in root.findall('.//a:t',NS)],[f'Entry {r} {c}' for r in range(6) for c in range(4)])
        self.assertEqual({n.get('sz') for n in root.findall('.//a:rPr',NS)},{'1200'})
        self.assertEqual(recover_drawn_tables(result),(result,[]))
        self.assertEqual(root.find('.//a:graphicData',NS).get('uri'),'http://schemas.openxmlformats.org/drawingml/2006/table')

    def test_cards_without_rules_and_incomplete_or_rotated_grids_stay_untouched(self):
        for mode in ('no_rules','missing_cell','rotated','filled'):
            parts=self.fixture(rules=mode!='no_rules')
            part=next(iter(parts));root=xml(parts[part]);shape=root.find('.//p:sp',NS)
            if mode=='missing_cell':shape.getparent().remove(shape)
            if mode=='rotated':shape.find('p:spPr/a:xfrm',NS).set('rot','5400000')
            if mode=='filled':shape.find('p:spPr',NS).remove(shape.find('p:spPr/a:noFill',NS))
            parts[part]=etree.tostring(root)
            result,changes=recover_drawn_tables(parts)
            self.assertEqual(changes,[],mode)
            self.assertEqual(result,parts,mode)


if __name__=='__main__':unittest.main()
