"""Pinned Google Fonts only. Create static faces for consistent native
measurement/LibreOffice. Never run on font bytes or URLs from a user's PPTX."""
import hashlib
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont,setRibbiBits

logging.disable(logging.WARNING)

def static_face(font,location):
    try:
        return instantiateVariableFont(font,location,inplace=False,optimize=False,updateFontNames=True)
    except ValueError as error:
        # Some valid Google variable fonts omit intermediate weights in STAT.
        # Instancing still works; only fontTools' automatic naming fails.
        if 'STAT' not in str(error) and 'Cannot find Axis Values' not in str(error):raise
        face=instantiateVariableFont(font,location,inplace=False,optimize=False,updateFontNames=False)
        family=font['name'].getBestFamilyName()
        weight=face['OS/2'].usWeightClass
        italic=bool(location.get('ital',face['OS/2'].fsSelection&1))
        weight_name={100:'Thin',200:'ExtraLight',300:'Light',400:'Regular',500:'Medium',600:'SemiBold',700:'Bold',800:'ExtraBold',900:'Black'}.get(weight,str(weight))
        style=(weight_name+(' Italic' if italic else '')).replace('Regular Italic','Italic')
        legacy_family=family if weight in (400,700) else family+' '+weight_name
        legacy_style=style if weight in (400,700) else ('Italic' if italic else 'Regular')
        ps_name=re.sub(r'[^a-zA-Z0-9-]','',family)+'-'+style.replace(' ','')
        values={1:legacy_family,2:legacy_style,4:family+' '+style,6:ps_name[:63],16:family,17:style}
        names=face['name']
        names.names=[n for n in names.names if n.nameID not in values]
        for name_id,value in values.items():
            names.setName(value,name_id,3,1,0x409)
            names.setName(value,name_id,1,0,0)
        face['head'].macStyle&=~3
        setRibbiBits(face)
        return face

def install(folder):
    faces=folder/'faces'
    result=[]
    for source in sorted((folder/'sources').glob('*')):
        if not source.name.endswith(('.ttf.source','.otf.source')):continue
        with TTFont(source) as font:
            if 'fvar' not in font:
                target=faces/source.name.removesuffix('.source')
                shutil.copyfile(source,target)
                candidates=[target]
            else:
                axes={a.axisTag:a.defaultValue for a in font['fvar'].axes}
                weight=next((a for a in font['fvar'].axes if a.axisTag=='wght'),None)
                weights=[w for w in range(100,1000,100) if weight and weight.minValue<=w<=weight.maxValue]
                italic=next((a for a in font['fvar'].axes if a.axisTag=='ital'),None)
                italics=[i for i in (0,1) if italic and italic.minValue<=i<=italic.maxValue]
                candidates=[]
                for w in weights or [None]:
                    for i in italics or [None]:
                        location={**axes,**({'wght':w} if w is not None else {}),**({'ital':i} if i is not None else {})}
                        face=static_face(font,location)
                        extension='.otf' if source.name.endswith('.otf.source') else '.ttf'
                        name=Path(source.name.removesuffix('.source')).stem.split('[')[0].replace(' ','')+f'-{w or 400}'+('-Italic' if i else '')+extension
                        target=faces/name
                        face.save(target);face.close();candidates.append(target)
            for target in candidates:
                with TTFont(target) as face:
                    result.append(dict(name=target.name,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),family=face['name'].getBestFamilyName(),style=face['name'].getBestSubFamilyName(),weight=face['OS/2'].usWeightClass))
    if not result:raise ValueError('presentation_font_no_faces')
    return dict(files=result)

if __name__=='__main__':
    print(json.dumps(install(Path(sys.argv[1]).resolve())))
