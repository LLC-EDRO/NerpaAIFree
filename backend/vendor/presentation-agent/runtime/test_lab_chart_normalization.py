import unittest
from runtime.security import xml,NS
from runtime.charts import normalize_chart
from runtime.contrast import readable_color

class ChartRepair(unittest.TestCase):
    def test_dark_background_repairs_explicit_and_inherited_ink_without_data_changes(self):
        from lxml import etree
        root=xml(f'<c:chartSpace xmlns:c="{NS["c"]}" xmlns:a="{NS["a"]}"><c:chart><c:plotArea><c:barChart><c:ser><c:val><c:numLit><c:pt idx="0"><c:v>240</c:v></c:pt></c:numLit></c:val></c:ser><c:dLbls><c:dLblPos val="outEnd"/><c:showVal val="1"/></c:dLbls></c:barChart><c:catAx><c:txPr><a:p><a:pPr><a:defRPr sz="1200"><a:solidFill><a:srgbClr val="555555"/></a:solidFill></a:defRPr></a:pPr></a:p></c:txPr></c:catAx></c:plotArea><c:legend/></c:chart></c:chartSpace>'.encode())
        normalize_chart(root,['#171C32','#FFFFFF','#51DED3'],'#171C32');first=etree.tostring(root)
        normalize_chart(root,['#171C32','#FFFFFF','#51DED3'],'#171C32')
        self.assertEqual(etree.tostring(root),first)
        self.assertEqual(root.xpath('.//c:val//c:v/text()',namespaces=NS),['240'])
        self.assertEqual(root.xpath('.//c:catAx//a:defRPr/@sz',namespaces=NS),['1200'])
        self.assertEqual(root.xpath('.//c:dLbls/c:txPr//a:srgbClr/@val',namespaces=NS),['FFFFFF'])
        self.assertEqual(root.xpath('.//c:catAx//a:srgbClr/@val',namespaces=NS),['FFFFFF'])

    def test_unreadable_pie_point_is_recolored_but_readable_point_is_preserved(self):
        from lxml import etree
        root=xml(f'<c:chartSpace xmlns:c="{NS["c"]}" xmlns:a="{NS["a"]}"><c:chart><c:plotArea><c:pieChart><c:ser><c:dPt><c:idx val="0"/><c:spPr><a:solidFill><a:srgbClr val="171C32"/></a:solidFill></c:spPr></c:dPt><c:dPt><c:idx val="1"/><c:spPr><a:solidFill><a:srgbClr val="51DED3"/></a:solidFill></c:spPr></c:dPt><c:val><c:numLit><c:pt idx="0"><c:v>10</c:v></c:pt><c:pt idx="1"><c:v>7</c:v></c:pt></c:numLit></c:val></c:ser><c:dLbls><c:dLblPos val="ctr"/><c:showVal val="1"/></c:dLbls></c:pieChart></c:plotArea></c:chart></c:chartSpace>'.encode())
        normalize_chart(root,['#171C32','#51DED3','#FF8570'],'#171C32');first=etree.tostring(root)
        normalize_chart(root,['#171C32','#51DED3','#FF8570'],'#171C32')
        self.assertEqual(etree.tostring(root),first)
        self.assertEqual(root.xpath('.//c:dPt/c:spPr/a:solidFill/a:srgbClr/@val',namespaces=NS),['FF8570','51DED3'])
        self.assertEqual(root.xpath('.//c:val//c:v/text()',namespaces=NS),['10','7'])
        self.assertEqual(root.xpath('.//c:dLbl/c:showVal/@val',namespaces=NS),['1','1'])
        from runtime.contrast import ratio
        for label,color in zip(root.findall('.//c:dLbl',NS),['#FF8570','#51DED3']):
            ink='#'+label.find('c:txPr//a:srgbClr',NS).get('val')
            self.assertGreaterEqual(ratio(ink,color),4.5)
    def test_doughnut_defaults_and_labels_are_idempotent_and_keep_values(self):
        root=xml(f'<c:chartSpace xmlns:c="{NS["c"]}" xmlns:a="{NS["a"]}"><c:chart><c:plotArea><c:doughnutChart><c:ser><c:idx val="0"/><c:order val="0"/><c:val><c:numLit><c:ptCount val="2"/><c:pt idx="0"><c:v>60</c:v></c:pt><c:pt idx="1"><c:v>40</c:v></c:pt></c:numLit></c:val></c:ser></c:doughnutChart></c:plotArea></c:chart></c:chartSpace>'.encode())
        for _ in range(2):normalize_chart(root,['#163248','#008B82','#FFFFFF'])
        self.assertEqual(root.xpath('.//c:holeSize/@val',namespaces=NS),['50'])
        self.assertEqual(root.xpath('.//c:val//c:v/text()',namespaces=NS),['60','40'])
        self.assertEqual(len(root.findall('.//c:dPt',NS)),2)
        self.assertEqual(root.xpath('.//c:dLbls/c:showPercent/@val',namespaces=NS),['1'])
        self.assertEqual(root.xpath('.//c:dLbls/c:dLblPos/@val',namespaces=NS),['ctr'])
        self.assertEqual(root.xpath('.//c:dLbl/c:txPr//a:srgbClr/@val',namespaces=NS),['FFFFFF','000000'])
        self.assertEqual(root.xpath('.//c:dLbl/c:showVal/@val',namespaces=NS),['0','0'])
        self.assertEqual(root.xpath('.//c:dLbl/c:showSerName/@val',namespaces=NS),['0','0'])
        self.assertEqual(root.xpath('.//c:dLbl/c:showPercent/@val',namespaces=NS),['1','1'])
    def test_only_unreadable_colors_use_available_palette(self):
        self.assertEqual(readable_color('#183C45','#102D36',['#F5F3EC','#008B82']),'#F5F3EC')
        self.assertIsNone(readable_color('#F5F3EC','#102D36',['#FFFFFF']))
        self.assertIsNone(readable_color('#183C45','#102D36',['#102D36']))
    def test_negative_bar_labels_are_outside_plot_without_changing_values(self):
        root=xml(f'<c:chartSpace xmlns:c="{NS["c"]}"><c:chart><c:plotArea><c:barChart><c:barDir val="bar"/><c:ser><c:val><c:numLit><c:ptCount val="2"/><c:pt idx="0"><c:v>-8</c:v></c:pt><c:pt idx="1"><c:v>12</c:v></c:pt></c:numLit></c:val></c:ser><c:axId val="1"/><c:axId val="2"/></c:barChart><c:catAx><c:axId val="1"/><c:tickLblPos val="nextTo"/><c:crossAx val="2"/></c:catAx></c:plotArea></c:chart></c:chartSpace>'.encode())
        normalize_chart(root);normalize_chart(root)
        self.assertEqual(root.xpath('.//c:catAx/c:tickLblPos/@val',namespaces=NS),['low'])
        self.assertEqual(root.xpath('.//c:val//c:v/text()',namespaces=NS),['-8','12'])
    def test_unstacked_areas_do_not_erase_other_series_and_keep_explicit_alpha(self):
        root=xml(f'<c:chartSpace xmlns:c="{NS["c"]}" xmlns:a="{NS["a"]}"><c:chart><c:plotArea><c:areaChart><c:grouping val="standard"/><c:ser><c:spPr><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill></c:spPr></c:ser><c:ser><c:spPr><a:solidFill><a:srgbClr val="0000FF"><a:alpha val="70000"/></a:srgbClr></a:solidFill></c:spPr></c:ser></c:areaChart></c:plotArea></c:chart></c:chartSpace>'.encode())
        normalize_chart(root);normalize_chart(root)
        self.assertEqual(root.xpath('.//a:alpha/@val',namespaces=NS),['55000','70000'])
