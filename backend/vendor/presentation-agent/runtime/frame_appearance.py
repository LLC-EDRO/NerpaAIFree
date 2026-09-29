"""Prove transparent placeholder appearance across master/layout/slide layers."""
from runtime.security import NS

def transparent_inheritance(nodes):
    fill=False
    line=False
    for node in nodes:
        if node.find('p:style',NS) is not None:return False
        prop=node.find('p:spPr',NS)
        if prop is None:continue
        if prop.xpath('./*[not(self::a:xfrm or self::a:prstGeom[@prst="rect"] or self::a:noFill or self::a:ln[a:noFill] or self::a:effectLst[not(*)])]',namespaces=NS):return False
        if prop.find('a:noFill',NS) is not None:fill=True
        if prop.find('a:ln/a:noFill',NS) is not None:line=True
    return fill and line
