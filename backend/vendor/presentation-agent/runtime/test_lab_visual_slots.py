import unittest
from runtime.fill import source_image_assignments
from runtime.layout_metadata import visual_slots,has_text
from runtime.security import NS
class VisualSlots(unittest.TestCase):
    def test_empty_picture_placeholder_and_placeholder_with_text(self):
        part='ppt/slides/any.xml'
        parts={part:f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:sp><p:nvSpPr><p:cNvPr id="917"/><p:cNvSpPr/><p:nvPr><p:ph type="pic"/></p:nvPr></p:nvSpPr></p:sp></p:sld>'.encode()}
        item=dict(shape_id=917,object_kind='shape',placeholder_type='pic',source_part=part,
                  geometry=dict(x_emu=0,y_emu=0,width_emu=100*12700,height_emu=80*12700))
        self.assertFalse(has_text(item))
        slots=visual_slots([item],dict(slots=[]),{},parts)
        self.assertEqual(len(slots),1)
        self.assertEqual(slots[0]['kind'],'placeholder')
        item['rich_text']={'paragraphs':[{'runs':[{'text':'Actual text'}]}]}
        self.assertTrue(has_text(item))
        self.assertEqual(visual_slots([item],dict(slots=[]),{},parts),[])
    def setUp(self):
        self.slot=dict(shapeId=42,kind='picture',box=dict(x=10,y=10,w=100,h=80),aspectRatio=1.25,protected=False,requiresOpaque=False)
        self.slide=dict(native=dict(mode='source'),visualSlotsVersion=1,images=[dict(shapeId=42,slotIndex=0,image='generated.png',background='transparent')])
    def test_exact_native_object_binding(self):
        actual=source_image_assignments({},dict(visualSlots=[self.slot]),self.slide)
        self.assertEqual(actual[0]['imageShapeId'],42)
        self.assertEqual(actual[0]['imageBox'],self.slot['box'])
    def test_no_size_based_exclusion_of_photo_underlay(self):
        self.slot.update(box=dict(x=0,y=0,w=720,h=405),requiresOpaque=True)
        self.slide['images'][0]['background']='opaque'
        self.assertTrue(source_image_assignments({},dict(visualSlots=[self.slot]),self.slide)[0]['requiresOpaque'])
    def test_protected_objects_and_stale_indices_cannot_be_replaced(self):
        for change in [dict(protected=True),dict(shapeId=43)]:
            with self.assertRaisesRegex(ValueError,'ambiguous'):
                source_image_assignments({},dict(visualSlots=[{**self.slot,**change}]),self.slide)
    def test_transparent_backdrop_and_duplicate_assignments_rejected(self):
        with self.assertRaisesRegex(ValueError,'requires_opaque'):
            source_image_assignments({},dict(visualSlots=[{**self.slot,'requiresOpaque':True}]),self.slide)
        self.slide['images']*=2
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            source_image_assignments({},dict(visualSlots=[self.slot]),self.slide)
    def test_keep_is_no_assignment(self):
        self.slide['images']=[]
        self.assertEqual(source_image_assignments({},dict(visualSlots=[self.slot]),self.slide),[])
    def test_rebuilt_slide_keeps_native_picture_binding_and_protection(self):
        self.slide['native'].update(mode='rebuild',rebuild={'texts':[],'pictures':[],'tables':[]})
        actual=source_image_assignments({},dict(visualSlots=[self.slot]),self.slide)
        self.assertEqual(actual[0]['imageShapeId'],42)
        self.assertEqual(actual[0]['imageBox'],self.slot['box'])
        self.slot['protected']=True
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            source_image_assignments({},dict(visualSlots=[self.slot]),self.slide)
