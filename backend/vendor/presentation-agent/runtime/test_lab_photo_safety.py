import unittest,io
from PIL import Image
from runtime.photo_safety import safe_cutout_box,replacement_photo_issues
from runtime.fill import contain_source_cutout
from runtime.geometry import intersection
class CutoutSafety(unittest.TestCase):
    def test_cutout_uses_free_native_frame_without_moving_text(self):
        box=dict(x=600,y=0,w=360,h=300);text=dict(key='caption',x=500,y=170,w=170,h=50)
        safe=safe_cutout_box(box,[text],{'caption':'Text'})
        self.assertIsNotNone(safe);self.assertEqual(intersection(safe,text),0)
        metadata=dict(visualSlots=[dict(shapeId=42,box=box,requiresOpaque=False)])
        slide=dict(visualSlotsVersion=1,native=dict(mode='source',fields={'caption':'Text'}),images=[dict(shapeId=42,image='x',background='transparent')])
        self.assertEqual(replacement_photo_issues(dict(slots=[text]),metadata,slide),[])
        raw=Image.new('RGBA',(360,300),(0,0,0,0));raw.paste((30,60,90,255),(10,10,350,290));data=io.BytesIO();raw.save(data,format='PNG')
        region=dict(x=(safe['x']-box['x'])/box['w'],y=(safe['y']-box['y'])/box['h'],w=safe['w']/box['w'],h=safe['h']/box['h'])
        out=Image.open(io.BytesIO(contain_source_cutout(data.getvalue(),1.2,region)))
        x,y,r,b=out.getchannel('A').getbbox();paint=dict(x=box['x']+x,y=box['y']+y,w=r-x,h=b-y)
        self.assertEqual(intersection(paint,text),0)
    def test_no_room_reports_image_not_text_repair(self):
        box=dict(x=0,y=0,w=100,h=100);text=dict(key='full',**box)
        self.assertIsNone(safe_cutout_box(box,[text],{'full':'Text'}))
        issues=replacement_photo_issues(dict(slots=[text]),dict(visualSlots=[dict(shapeId=9,box=box)]),dict(visualSlotsVersion=1,native=dict(mode='source',fields={'full':'Text'}),images=[dict(shapeId=9,image='x',background='transparent')]))
        self.assertEqual(issues[0]['imageShapeId'],9)
        self.assertEqual(issues[0]['details'],['generated_photo_no_safe_region'])
    def test_opaque_picture_uses_safe_region_without_distorting_source_shape(self):
        frame=dict(x=100,y=20,w=300,h=200);text=dict(key='caption',x=80,y=40,w=60,h=80)
        slot=dict(shapeId=7,kind='picture',box=frame,requiresOpaque=False)
        slide=dict(visualSlotsVersion=1,native=dict(mode='source',fields={'caption':'Filled copy'}),images=[dict(shapeId=7,image='asset',background='opaque')])
        self.assertEqual(replacement_photo_issues(dict(slots=[text]),dict(visualSlots=[slot]),slide),[])
        safe=safe_cutout_box(frame,[text],slide['native']['fields'])
        region=dict(x=(safe['x']-frame['x'])/frame['w'],y=(safe['y']-frame['y'])/frame['h'],w=safe['w']/frame['w'],h=safe['h']/frame['h'])
        data=io.BytesIO();Image.new('RGB',(600,400),'navy').save(data,format='PNG')
        out=Image.open(io.BytesIO(contain_source_cutout(data.getvalue(),1.5,region,True)))
        left,top,right,bottom=out.getchannel('A').getbbox()
        painted=dict(x=frame['x']+left/out.width*frame['w'],y=frame['y']+top/out.height*frame['h'],w=(right-left)/out.width*frame['w'],h=(bottom-top)/out.height*frame['h'])
        self.assertEqual(intersection(painted,text),0)
