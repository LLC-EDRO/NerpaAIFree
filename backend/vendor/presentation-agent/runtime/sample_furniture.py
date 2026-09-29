"""Remove explicit template prompts from an OUTPUT copy, never real branding."""
import re
from runtime.security import NS

SAMPLE = re.compile(r'^(?:your\s*(?:footer|header|date|logo|company(?:\s+name)?)(?:\s+here)?|'
                    r'(?:insert|add)\s+(?:your\s+)?(?:footer|header|date|logo)(?:\s+here)?|'
                    r'(?:ваш[аи]?|вставьте)\s+(?:логотип|дата|колонтитул|название компании))$', re.I)
AUTHORING_HINT = re.compile(
    r'^(?:(?:иконки|иллюстрации|изображения|фотографии|шрифты)\s+'
    r'(?:можно|следует|нужно)\s+(?:брать|скачать|скачивать|загрузить|загружать)\s+(?:из|с)\s+|'
    r'(?:icons|illustrations|images|photos|fonts)\s+(?:can|may|should)\s+be\s+'
    r'(?:downloaded|taken|obtained)\s+from\s+)', re.I)
ATTRIBUTION = re.compile(r'©|copyright|\b(?:credits?|attribution|licen[cs]e|reserved)\b|'
                         r'авторск|лиценз|правообладател|указани\S*\s+автор', re.I)

def is_sample_furniture(value):
    value = ' '.join(value.split()).strip(' []<>')
    # Source-only instructions are not branding. Keep any mixed legal/credit
    # notice intact; never infer this from a vendor name, URL, colour or ID.
    return bool(SAMPLE.fullmatch(value) or (AUTHORING_HINT.match(value) and not ATTRIBUTION.search(value)))


def clean_sample_furniture(root):
    for shape in root.findall('.//p:sp', NS):
        texts = shape.findall('p:txBody//a:t', NS)
        value = ' '.join(''.join(t.text or '' for t in texts).split()).strip(' []<>')
        if is_sample_furniture(value):
            # Keep geometry and decoration; only demonstrably example wording
            # goes. A real company, copyright or attribution is never erased.
            for text in texts:
                text.text = ''
