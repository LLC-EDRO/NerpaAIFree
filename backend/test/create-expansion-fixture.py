"""A valid existing fixture package with one small editable sample heading."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as E
import sys
root=Path(__file__).resolve().parents[2];target=Path(sys.argv[1]);target.parent.mkdir(parents=True,exist_ok=True)
ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
with ZipFile(root/'fixtures/cobalt-editorial.pptx') as z:parts={n:z.read(n) for n in z.namelist()}
r=E.fromstring(parts['ppt/presentation.xml']);ids=r.find('p:sldIdLst',ns)
for n in list(ids)[1:]:ids.remove(n)
parts['ppt/presentation.xml']=E.tostring(r)
r=E.fromstring(parts['ppt/slides/slide1.xml']);tree=r.find('p:cSld/p:spTree',ns)
for n in list(tree):
 if n.tag.split('}')[-1] not in ['nvGrpSpPr','grpSpPr']:tree.remove(n)
for bg in r.findall('p:cSld/p:bg',ns):r.find('p:cSld',ns).remove(bg)
for identity,x,y,w,h,size,text in [(901,20,20,60,40,22,'Test'),(902,20,160,450,60,16,'Protected neighbouring text')]:
 tree.append(E.fromstring(f'''<p:sp xmlns:p="{ns['p']}" xmlns:a="{ns['a']}"><p:nvSpPr><p:cNvPr id="{identity}" name="Text {identity}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x*12700}" y="{y*12700}"/><a:ext cx="{w*12700}" cy="{h*12700}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr anchor="t" lIns="0" rIns="0" tIns="0" bIns="0" wrap="square"><a:noAutofit/></a:bodyPr><a:lstStyle/><a:p><a:pPr algn="l"/><a:r><a:rPr lang="ru-RU" sz="{size*100}"><a:solidFill><a:srgbClr val="222222"/></a:solidFill><a:latin typeface="DejaVu Sans"/><a:ea typeface="DejaVu Sans"/><a:cs typeface="DejaVu Sans"/></a:rPr><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>'''))
parts['ppt/slides/slide1.xml']=E.tostring(r)
with ZipFile(target,'w',ZIP_DEFLATED) as z:
 for name,data in parts.items():z.writestr(name,data)
