import unittest
from runtime.security import NS,xml
from runtime.image_markers import image_instruction,labelled_image_frames
from runtime.layout_metadata import visual_slots
class ImageMarkersTest(unittest.TestCase):
    def scene(self,text='Вставить\nфото'):
        part='ppt/slides/arbitrary.xml'
        parts={part:f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:sp><p:nvSpPr><p:cNvPr id="81"/></p:nvSpPr><p:spPr><a:prstGeom prst="roundRect"/><a:solidFill><a:srgbClr val="eeeeee"/></a:solidFill></p:spPr></p:sp><p:sp><p:nvSpPr><p:cNvPr id="93"/></p:nvSpPr><p:spPr><a:noFill/></p:spPr></p:sp></p:sld>'.encode()}
        def shape(id,x,y,w,h):return dict(shape_id=id,object_kind='shape',source_part=part,geometry=dict(x_emu=x*12700,y_emu=y*12700,width_emu=w*12700,height_emu=h*12700))
        items=[shape(81,20,20,300,160),shape(93,130,85,80,25)]
        layout=dict(slots=[dict(key='instruction',shapeId=93,text=text,x=130,y=85,w=80,h=25)])
        return items,layout,parts
    def test_explicit_instruction_is_mapped_to_original_frame_not_label(self):
        items,layout,parts=self.scene();slots=visual_slots(items,layout,{},parts)
        self.assertEqual(len(slots),1);self.assertEqual(slots[0]['shapeId'],81)
        self.assertEqual(slots[0]['kind'],'placeholder');self.assertFalse(slots[0]['requiresOpaque'])
        self.assertEqual(slots[0]['detection']['markerFieldKeys'],['instruction'])
    def test_generic_captions_and_prose_do_not_make_image_slots(self):
        for text in ['Фото','Наша команда','Вставить фото в отчёт и обсудить','Рост 30%','Картинка продукта']:
            items,layout,parts=self.scene(text);self.assertEqual(visual_slots(items,layout,{},parts),[],text)
        for text in ['Insert image here','Image placeholder','Место для изображения','Вставить фото']:
            self.assertTrue(image_instruction(text),text)
    def test_frame_containing_real_content_is_not_replaced(self):
        items,layout,parts=self.scene();layout['slots'].append(dict(key='body',shapeId=100,text='Important content',x=30,y=30,w=100,h=30))
        self.assertEqual(labelled_image_frames(items,layout,{},parts),{})
    def test_logo_rotation_and_unframed_instruction_are_rejected(self):
        items,layout,parts=self.scene();self.assertEqual(labelled_image_frames(items,layout,{81:'logo'},parts),{})
        items[0]['geometry_full']={'rotation_deg':10};self.assertEqual(labelled_image_frames(items,layout,{},parts),{})
        self.assertEqual(labelled_image_frames(items[1:],layout,{},parts),{})
    def test_existing_photo_or_icon_inside_candidate_is_not_erased(self):
        items,layout,parts=self.scene();items.append(dict(shape_id=333,object_kind='picture',geometry=dict(x_emu=40*12700,y_emu=40*12700,width_emu=20*12700,height_emu=20*12700)))
        self.assertEqual(labelled_image_frames(items,layout,{},parts),{})
    def test_shape_fill_requires_verified_marker_and_preserves_rounding(self):
        from runtime.fill import source_image_blip,source_image_assignments
        items,layout,parts=self.scene();slots=visual_slots(items,layout,{},parts)
        root=xml(next(iter(parts.values())));node=root.find('p:sp',NS)
        with self.assertRaisesRegex(ValueError,'ambiguous'):source_image_blip(node,'placeholder')
        assignment=source_image_assignments(layout,dict(visualSlots=slots),dict(visualSlotsVersion=1,native=dict(mode='source'),images=[dict(shapeId=81,slotIndex=0,image='test')]))[0]
        self.assertTrue(assignment['confirmedImageFrame'])
        blip=source_image_blip(node,'placeholder',assignment['confirmedImageFrame'])
        self.assertEqual(blip.tag,'{'+NS['a']+'}blip')
        self.assertEqual(node.find('p:spPr/a:prstGeom',NS).get('prst'),'roundRect')
