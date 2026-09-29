"""Source object geometry shared by native parsing and legacy rendering."""

def box(item):
    g = (item.get('geometry_full') or {}).get('absolute_bbox') or item.get('geometry') or {}
    return dict(x=(g.get('x_emu') or 0)/12700, y=(g.get('y_emu') or 0)/12700,
                w=(g.get('width_emu') or 0)/12700, h=(g.get('height_emu') or 0)/12700)


def intersection(a, b):
    w = max(0, min(a['x']+a['w'], b['x']+b['w'])-max(a['x'], b['x']))
    h = max(0, min(a['y']+a['h'], b['y']+b['h'])-max(a['y'], b['y']))
    return w*h


def inside(a, b, tolerance=.1):
    return (a['x'] >= b['x']-tolerance and a['y'] >= b['y']-tolerance
            and a['x']+a['w'] <= b['x']+b['w']+tolerance and a['y']+a['h'] <= b['y']+b['h']+tolerance)

def rectangular_geometry(shape):
    """Recognize native rectangles and equivalent imported four-corner paths.
    Curves, holes, inset paths and crossed polygons are not plain containers.
    """
    from runtime.security import NS
    preset=shape.find('p:spPr/a:prstGeom',NS)
    if preset is not None:return preset.get('prst')=='rect'
    paths=shape.findall('p:spPr/a:custGeom/a:pathLst/a:path',NS)
    if len(paths)!=1:return False
    path=paths[0];commands=[c.tag.rsplit('}',1)[-1] for c in path]
    if commands not in (['moveTo','lnTo','lnTo','lnTo','close'],['moveTo','lnTo','lnTo','lnTo','lnTo','close']):return False
    try:
        w,h=int(path.get('w')),int(path.get('h'))
        points=[(int(p.get('x')),int(p.get('y'))) for p in path.findall('*/a:pt',NS)]
    except (TypeError,ValueError):return False
    if len(points)==5 and points[-1]==points[0]:points.pop()
    return (w>0 and h>0 and len(points)==4 and set(points)=={(0,0),(w,0),(w,h),(0,h)}
        and all((a[0]==b[0])!=(a[1]==b[1]) for a,b in zip(points,points[1:]+points[:1])))
