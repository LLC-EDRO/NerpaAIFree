import unittest
from copy import deepcopy
from runtime.source_composition import add_source_contacts, source_line_contact
from runtime.rebuild import validate


class SourceCompositionTest(unittest.TestCase):
    def fixture(self):
        page=dict(x=0,y=0,w=700,h=400)
        anchors=[dict(key='r_title' if i==0 else 'r_metric',sourceKey=str(i),shapeId=20+i,
            x=20+i*250,y=90,w=180,h=150,size=100,font='Liberation Sans',bold=True,
            color='#0077FF',align='left',verticalAlign='bottom',allowFrameOverflow=True,repairRegion=page)
            for i in range(2)]
        pictures=[dict(shapeId=10+i,box=dict(x=27+i*250,y=220,w=180,h=2)) for i in range(2)]
        profile=dict(version=8,fingerprint='test',fonts=['Liberation Sans'],colors=['#0077FF'],
            page=page,textAnchors=anchors,pictures=pictures,decorations=[],charts=[],tables=[],protectedBoxes=[],keepShapeIds=[])
        return profile,[dict(key=str(i),text=str(7+i)) for i in range(2)],{10:0,11:1,20:2,21:3}

    def test_contacts_and_metric_peers_are_source_derived(self):
        p,slots,order=self.fixture();add_source_contacts(p,slots,order)
        self.assertEqual(p['metricAlignmentGroups'][0]['keys'],['r_title','r_metric'])
        self.assertEqual(p['textAnchors'][0]['sourceLineContacts'][0]['shapeId'],10)

    def test_background_line_allows_small_horizontal_expansion_without_lifting_text(self):
        p,slots,order=self.fixture();add_source_contacts(p,slots,order)
        texts=[dict(a,size=70,x=a['x']+7) for a in p['textAnchors']]
        native=dict(rebuild=dict(version=1,fingerprint='test',texts=texts,pictures=[dict(shapeId=o['shapeId'],**o['box']) for o in p['pictures']],charts=[],tables=[]),fields={'r_title':'42','r_metric':'57'})
        self.assertFalse(any(i['details']==['rebuild_overlap'] for i in validate(p,native,0)))
        legacy=deepcopy(p)
        for a in legacy['textAnchors']:a.pop('sourceLineContacts')
        self.assertTrue(any(i['details']==['rebuild_overlap'] for i in validate(legacy,native,0)))

    def test_photos_foreground_and_new_contacts_are_not_waived(self):
        for change in ('photo','foreground','new','protected'):
            p,slots,order=self.fixture()
            if change=='photo':p['pictures'][0]['box']['h']=80
            if change=='foreground':order[10]=99
            if change=='new':p['pictures'][0]['box']['y']=300
            if change=='protected':p['keepShapeIds']=[10]
            add_source_contacts(p,slots,order)
            self.assertNotIn('sourceLineContacts',p['textAnchors'][0],change)

    def test_unrelated_styles_and_prose_do_not_become_metric_rows(self):
        for change in ('prose','stagger','font'):
            p,slots,order=self.fixture()
            if change=='prose':slots[1]['text']='An ordinary heading'
            if change=='stagger':p['textAnchors'][1]['y']+=30
            if change=='font':p['textAnchors'][1]['font']='Liberation Serif'
            add_source_contacts(p,slots,order)
            self.assertNotIn('metricAlignmentGroups',p,change)

    def test_contacts_do_not_allow_moving_a_line_or_leaving_own_region(self):
        p,slots,order=self.fixture();add_source_contacts(p,slots,order)
        text=deepcopy(p['textAnchors'][0]);line=dict(shapeId=10,**p['pictures'][0]['box'])
        self.assertTrue(source_line_contact(p,text,line))
        self.assertFalse(source_line_contact(p,text,dict(line,y=250)))
        self.assertFalse(source_line_contact(p,dict(text,x=690),line))

if __name__=='__main__':unittest.main()
