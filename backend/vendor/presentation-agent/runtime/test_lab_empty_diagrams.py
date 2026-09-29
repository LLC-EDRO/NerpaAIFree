import unittest
from runtime.empty_diagrams import empty_diagram_nodes
from runtime.security import NS,xml
class EmptyDiagramTest(unittest.TestCase):
    def source(self,connected=True,text=''):
        shapes=[]
        for i in range(3):
            x=(20+i*100)*12700
            shapes.append(f'<p:sp><p:nvSpPr><p:cNvPr id="{i+83}"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="254000"/><a:ext cx="762000" cy="381000"/></a:xfrm><a:prstGeom prst="rect"/><a:noFill/><a:ln><a:solidFill/></a:ln></p:spPr><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>')
            if connected:shapes.append(f'<p:cxnSp><p:spPr><a:xfrm><a:off x="{x}" y="254000"/><a:ext cx="0" cy="100000"/></a:xfrm></p:spPr></p:cxnSp>')
        return xml((f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree>'+''.join(shapes)+'</p:spTree></p:cSld></p:sld>').encode())
    def test_connected_empty_nodes_are_detected_without_template_ids(self):
        self.assertEqual(empty_diagram_nodes(self.source()),[83,84,85])
    def test_decorative_or_filled_rectangles_are_not_empty_diagrams(self):
        self.assertEqual(empty_diagram_nodes(self.source(False)),[])
        self.assertEqual(empty_diagram_nodes(self.source(text='Text')),[])
