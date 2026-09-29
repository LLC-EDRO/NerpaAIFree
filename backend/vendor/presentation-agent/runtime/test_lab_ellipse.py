import unittest
import math
from types import SimpleNamespace as Obj
from runtime.shape_text_geometry import ShapeTextGeometry
from runtime.security import NS

class EllipseGeometryTest(unittest.TestCase):
    def shape(self, adjustment=''):
        part='ppt/slides/slide2.xml'
        data=f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="723"/></p:nvSpPr><p:spPr><a:prstGeom prst="ellipse"><a:avLst>{adjustment}</a:avLst></a:prstGeom></p:spPr></p:sp></p:spTree></p:cSld></p:sld>'.encode()
        shape=Obj(source_part=part,shape_id=723,text_frame=Obj(shape_geometry='ellipse',style_layers=[]))
        return ShapeTextGeometry({part:data}),shape

    def test_native_text_rectangle_respects_both_ellipse_axes(self):
        geometry,shape=self.shape()
        dx,dy,right,bottom=geometry.insets(shape,2000000,1000000)
        self.assertEqual((dx,dy),(right,bottom))
        self.assertEqual(dx,math.ceil(2000000*(1-math.sqrt(.5))/2))
        self.assertEqual(dy,math.ceil(1000000*(1-math.sqrt(.5))/2))
        self.assertGreater(dx,0)
        self.assertLess(2*dx,2000000)

    def test_unknown_ellipse_adjustments_are_not_approximated(self):
        geometry,shape=self.shape('<a:gd name="custom" fmla="val 10"/>')
        self.assertIsNone(geometry.insets(shape,2000000,1000000))
