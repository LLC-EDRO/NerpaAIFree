import unittest
import pymupdf
from runtime.render_quality import inspect_page


class ClippedImageTest(unittest.TestCase):
    def page(self, clip=None, vector=False):
        document = pymupdf.open()
        page = document.new_page(width=200, height=200)
        page.insert_text((20, 35), 'Visible title', fontsize=18)
        if vector:
            page.draw_rect(page.rect, color=None, fill=(.1, .3, .8))
        else:
            image = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 1, 1), False)
            image.set_pixel(0, 0, (20, 50, 190))
            page.insert_image(page.rect, pixmap=image)
        if clip:
            xref = page.get_contents()[-1]
            document.update_stream(xref, ('q ' + clip + ' re W n\n').encode() + document.xref_stream(xref) + b'\nQ')
        return document, page

    def issues(self, page):
        return inspect_page(page, [dict(key='title', value='Visible title', rect=pymupdf.Rect(0, 0, 200, 60))])['issues']

    def test_cropped_image_and_path_do_not_hide_visible_title(self):
        for vector in (False, True):
            document, page = self.page('0 0 200 100', vector)
            self.assertEqual(self.issues(page), [])
            document.close()

    def test_actual_image_and_path_cover_still_block(self):
        for vector in (False, True):
            for clip in (None, '0 140 200 60'):
                document, page = self.page(clip, vector)
                self.assertTrue(any('rendered_text_occluded' in issue['details'] for issue in self.issues(page)))
                document.close()

    def test_nested_clips_intersect_and_pop_restores_scope(self):
        document, page = self.page()
        xref = page.get_contents()[-1]
        original = document.xref_stream(xref)
        document.update_stream(xref, b'q 0 0 200 100 re W n q 0 0 200 200 re W n\n' + original + b'\nQ Q')
        self.assertEqual(self.issues(page), [])
        page.draw_rect(page.rect, color=None, fill=(0, 0, 0))
        self.assertTrue(any('rendered_text_occluded' in issue['details'] for issue in self.issues(page)))
        document.close()


if __name__ == '__main__':
    unittest.main()
