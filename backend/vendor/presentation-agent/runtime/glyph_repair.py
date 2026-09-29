"""Report an exact, font-supported spelling for missing typographic glyphs."""
from runtime.fonts import TemplateFontResolver
from app.text.native import _font_coverage

EQUIVALENTS={'→':'->','←':'<-','↔':'<->','⇒':'=>','≤':'<=','≥':'>=','\u2011':'-','\u202f':' ','\u00a0':' '}

def replacement_guidance(family,bold,value):
    face=TemplateFontResolver().resolve(family,bold=bold,italic=False)
    if not face.path:return {}
    coverage=_font_coverage(str(face.path))
    missing=sorted({c for c in value if not c.isspace() and ord(c) not in coverage})
    return dict(unsupportedCharacters=missing,characterReplacements={c:EQUIVALENTS[c] for c in missing
        if c in EQUIVALENTS and all(ord(v) in coverage for v in EQUIVALENTS[c])})
