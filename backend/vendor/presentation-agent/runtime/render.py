"""Render the actual native PPTX, not an approximation from normalized JSON."""
import os
import json
import subprocess
import tempfile
from pathlib import Path
import pymupdf
import io
import zipfile
from lxml import etree
from runtime.security import read_package,xml,NS
from runtime.fonts import TemplateFontResolver as FontResolver, font_face_aliases

def font_environment(work, families=()):
    # Headless builds may use Fontconfig even on macOS, bypassing CoreText's
    # user font directory. Give rendering the SAME installed fonts as fitting.
    # Per-invocation config; no global renderer/system configuration is edited.
    root=etree.Element('fontconfig')
    if Path('/font-cache/fontconfig').is_dir():
        etree.SubElement(root,'cachedir').text='/font-cache/fontconfig'
    etree.SubElement(root,'cachedir').text=str(Path(os.environ.get('HOME',str(work)))/'font-cache')
    etree.SubElement(root,'include',ignore_missing='yes').text='/etc/fonts/fonts.conf'
    for folder in FontResolver.FONT_ROOTS:
        if folder.is_dir(): etree.SubElement(root,'dir').text=str(folder)
    # A PPTX may name "Family Light" although the TTF's family is "Family".
    # Merely adding the directory makes Fontconfig choose an unrelated family.
    # Match the same real family/weight/slant as the native text fitter.
    weights={100:'thin',200:'extralight',300:'light',400:'regular',500:'medium',
             600:'demibold',700:'bold',800:'extrabold',900:'black'}
    for alias,face in font_face_aliases(families):
        for bold in (True,False):
            rule=etree.SubElement(root,'match',target='pattern')
            test=etree.SubElement(rule,'test',name='family',compare='eq')
            etree.SubElement(test,'string').text=alias
            if bold:
                test=etree.SubElement(rule,'test',name='weight',compare='more_eq')
                etree.SubElement(test,'const').text='demibold'
            edit=etree.SubElement(rule,'edit',name='family',mode='assign',binding='strong')
            etree.SubElement(edit,'string').text=face['family']
            widths={1:'ultracondensed',2:'extracondensed',3:'condensed',4:'semicondensed',5:'normal',
                    6:'semiexpanded',7:'expanded',8:'extraexpanded',9:'ultraexpanded'}
            if face['width'] in widths:
                edit=etree.SubElement(rule,'edit',name='width',mode='assign')
                etree.SubElement(edit,'const').text=widths[face['width']]
            edit=etree.SubElement(rule,'edit',name='weight',mode='assign')
            weight=max(700,face['weight']) if bold else face['weight']
            # OpenType permits intermediate weights; fontconfig has a
            # continuous scale between its documented weight constants.
            stops={100:0,200:40,300:50,400:80,500:100,600:180,700:200,800:205,900:210}
            if weight in weights:
                etree.SubElement(edit,'const').text=weights[weight]
            else:
                low=max(100,min(800,weight//100*100));fraction=max(0,min(1,(weight-low)/100))
                etree.SubElement(edit,'double').text=str(stops[low]+fraction*(stops[low+100]-stops[low]))
            if face['italic']:
                edit=etree.SubElement(rule,'edit',name='slant',mode='assign')
                etree.SubElement(edit,'const').text='italic'
    config=work/'fonts.conf'
    config.write_bytes(etree.tostring(root,xml_declaration=True,encoding='UTF-8'))
    return {**os.environ,'FONTCONFIG_FILE':str(config)}

def prepare_profile(work):
    user=work/'profile'/'user'
    user.mkdir(parents=True,exist_ok=True)
    (user/'registrymodifications.xcu').write_text('''<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>''')


def render(source, destination, binary):
    parts=read_package(source.read_bytes(),generated=source.name!='source.pptx')
    families={node.get('typeface') for name,data in parts.items() if name.startswith('ppt/') and name.endswith('.xml')
              for node in xml(data).iter() if node.get('typeface') and not node.get('typeface').startswith('+')}
    for name,data in parts.items():
        if name.startswith('ppt/charts/') and name.endswith('.xml'):
            root=xml(data)
            for node in root.xpath('//*[local-name()="externalData"]/*[local-name()="autoUpdate"]'):node.set('val','0')
            parts[name]=etree.tostring(root,xml_declaration=True,encoding='UTF-8')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nerpa-pptx-') as temp:
        work = Path(temp)
        warm=os.environ.get('PRESENTATION_WARM_RENDERER')=='1'
        if warm:
            configured=set(json.loads(Path('/work/renderer/families.json').read_text()))
            # An AI repair may choose another installed face. Render it with
            # its exact alias rules rather than reuse an incompatible profile.
            warm=all(alias in configured for alias,_ in font_face_aliases(families))
        renderer=Path('/work/renderer') if warm else work
        if not warm:prepare_profile(work)
        # Never open the immutable upload in a converter that could update it.
        input_path = work/'deck.pptx'
        with zipfile.ZipFile(input_path,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in parts.items():z.writestr(name,data)
        env={**os.environ,'FONTCONFIG_FILE':str(renderer/'fonts.conf')} if warm else font_environment(work,families)
        pdf = work/'deck.pdf'
        command=['/usr/bin/python3','-I','/opt/pptx/uno_convert.py',str(input_path),str(pdf)] if warm else [binary, '-env:UserInstallation='+ (renderer/'profile').as_uri(), '--headless', '--nologo', '--nodefault', '--norestore', '--convert-to', 'pdf:impress_pdf_Export', '--outdir', str(work), str(input_path)]
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False)
        if result.returncode in (-9, 137): raise ValueError('pptx_resource_limit')
        if result.returncode or not pdf.exists(): raise ValueError('pptx_render_failed')
        payload = pdf.read_bytes()
        (destination/'presentation.pdf').write_bytes(payload)
        with pymupdf.open(stream=payload, filetype='pdf') as document:
            for index, page in enumerate(document):
                page.get_pixmap(matrix=pymupdf.Matrix(1280/page.rect.width,1280/page.rect.width), alpha=False).save(destination/f'slide-{index}.png')
            return len(document)
