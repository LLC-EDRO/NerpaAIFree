"""Adapt the imported parser/analyzer without changing their complete JSON contracts."""
import json
import hashlib
import math
import gc
from runtime.model_store import write_model
from app.presentation.parser import PresentationParser
from app.presentation.template import TemplateAnalyzer, TemplateAnalyzerConfig
from runtime.text_frames import source_text_frame, TemplateTextFitter as NativeTextFitter, TemplateTextFrame as NativeTextFrame, overflow_guidance
from runtime.tables import table_slots, cell_frame, expanded_table_layout, apply_table_text_sizes
from runtime.charts import chart_spec, validate_charts
from runtime.security import read_package, slide_order, xml, NS
from runtime.fonts import choose_fonts, recover_marker_fonts, prepare_font_copy, font_report, materialize_table_fonts
from runtime.sample_furniture import is_sample_furniture
from runtime.preflight import layout_readiness
from runtime.shape_text_geometry import ShapeTextGeometry
from runtime.frame_store import read_frames
from runtime.textbox_repair import recover_textbox_bounds
from runtime.table_repair import recover_table_rows
from runtime.empty_diagrams import empty_diagram_nodes
from runtime.typography import inherited_text_color,inherited_vertical_alignment
from runtime.text_protection import retain_text_artwork
from runtime.drawn_tables import recover_drawn_tables

RECOVERY_VERSION = 12

def flatten(objects):
    for item in objects:
        yield item
        yield from flatten(item.children)

def text_of(item):
    return '\n'.join(''.join(r.text for r in p.runs) for p in item.rich_text.paragraphs) if item.rich_text else ''

def analyze(source, folder):
    parts = read_package(source.read_bytes())
    order = slide_order(parts)
    parts, drawn_tables = recover_drawn_tables(parts)
    prepared = prepare_font_copy(parts, folder, []) if drawn_tables else source
    model = PresentationParser().parse(prepared)
    if not model.passed:
        raise ValueError('pptx_parse_failed')
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    write_model(folder / 'parser.json', model)
    requirements = {}
    for slide in model.slides:
        for item in flatten(slide.objects):
            if item.rich_text and item.text_frame and (item.has_text_content or item.placeholder_type):
                for paragraph in source_text_frame(item).paragraphs:
                    if paragraph.font_family:
                        requirements.setdefault(paragraph.font_family,set()).add((paragraph.bold,paragraph.italic))
    shapes={s.source_object_id:s for slide in model.slides for s in flatten(slide.objects)}
    for table in model.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.effective_style.font_family:
                    requirements.setdefault(cell.effective_style.font_family,set()).add((cell.effective_style.font_weight==700,bool(cell.effective_style.italic)))
                shape=shapes.get(table.linked_object_id)
                if shape:
                    for paragraph in cell_frame(table,cell,shape).paragraphs:
                        if paragraph.font_family:requirements.setdefault(paragraph.font_family,set()).add((paragraph.bold,paragraph.italic))
    substitutions = choose_fonts(requirements)
    prepared_sha = None
    # Each recovery uses the effective result of the previous one: a missing
    # source face must not prevent measuring a recoverable degenerate textbox.
    if substitutions:
        parts=materialize_table_fonts(parts,model,substitutions)
        prepared = prepare_font_copy(parts, folder, substitutions)
        del model
        gc.collect()
        model = PresentationParser().parse(prepared)
        if not model.passed: raise ValueError('pptx_parse_failed')
        parts = read_package(prepared.read_bytes())
    parts, bullet_substitutions = recover_marker_fonts(parts, model)
    if bullet_substitutions:
        prepared = prepare_font_copy(parts, folder, [])
        del model
        gc.collect()
        model = PresentationParser().parse(prepared)
        if not model.passed: raise ValueError('pptx_parse_failed')
    parts, textbox_repairs = recover_textbox_bounds(parts, [item for slide in model.slides for item in flatten(slide.objects)])
    if textbox_repairs:
        prepared = prepare_font_copy(parts, folder, [])
        del model
        gc.collect()
        model = PresentationParser().parse(prepared)
        if not model.passed: raise ValueError('pptx_parse_failed')
    parts, table_repairs = recover_table_rows(parts, model)
    if table_repairs:
        prepared = prepare_font_copy(parts, folder, [])
        del model
        gc.collect()
        model = PresentationParser().parse(prepared)
        if not model.passed: raise ValueError('pptx_parse_failed')
    if drawn_tables or substitutions or bullet_substitutions or textbox_repairs or table_repairs:
        prepared_sha = hashlib.sha256(prepared.read_bytes()).hexdigest()
        parts = read_package(prepared.read_bytes())
    # The upstream parser orders part names. Respect actual PowerPoint slide ordering.
    model.slides.sort(key=lambda s: order.index(s.source_part))
    for index, slide in enumerate(model.slides):
        slide.slide_index = index
    template = TemplateAnalyzer(config=TemplateAnalyzerConfig(semantic_mode='off', vision_review_mode='off')).analyze(presentation_model=model).template_model
    write_model(folder / 'effective-parser.json', model)
    width = model.dimensions.width_emu / 12700
    height = model.dimensions.height_emu / 12700
    from runtime.page_numbers import pagination_padding,upper_page_marker
    page_roots={s.slide_id:xml(parts[s.source_part]) for s in model.slides}
    pagination=pagination_padding(page_roots.values(),width,height)
    for assignment in template.slide_assignments:
        root=page_roots.get(assignment.slide_id)
        if root is None:continue
        markers={int(s.find('p:nvSpPr/p:cNvPr',NS).get('id')) for s in root.findall('.//p:sp',NS) if s.find('p:nvSpPr/p:cNvPr',NS) is not None and upper_page_marker(s,width,height,pagination)}
        for role in assignment.role_assignments:
            if role.source_level=='slide' and role.source_open_xml_shape_id in markers:role.role='page_number'
    write_model(folder / 'analyzer.json', template)
    layouts, warnings, frames = [], [], {}
    tables={t.linked_object_id:t for t in model.tables if t.source_level=='slide'}
    assignments = {a.slide_id: {r.source_open_xml_shape_id: r for r in a.role_assignments if r.source_level == 'slide'} for a in template.slide_assignments}
    fitter = NativeTextFitter()
    text_geometry = ShapeTextGeometry(parts)
    for slide in model.slides:
        frames[slide.slide_id]={}
        roles = assignments.get(slide.slide_id, {})
        slots, pictures, problems, charts = [], [], [], []
        empty_nodes = empty_diagram_nodes(xml(parts[slide.source_part]))
        if empty_nodes:
            problems.append('Пустая схема содержит соединённые блоки без доступных текстовых полей. Для заполнения автоматически выбирается другой содержательный макет этого шаблона.')
        for item in flatten(slide.objects):
            if item.hidden or item.shape_id is None:
                continue
            role = roles.get(item.shape_id)
            geom = item.geometry_full.absolute_bbox if item.geometry_full else item.geometry
            if geom is None:
                continue
            box = dict(x=(geom.x_emu or 0)/12700, y=(geom.y_emu or 0)/12700, w=(geom.width_emu or 0)/12700, h=(geom.height_emu or 0)/12700)
            if box['w'] <= 0 or box['h'] <= 0:
                continue
            if item.object_kind in ('smartart', 'embedded_object'):
                problems.append('Слайд содержит SmartArt или встроенный объект: сохранён в разборе, автоматическое заполнение этого макета пока недоступно.')
            if item.object_kind == 'chart':
                try: charts.append({**chart_spec(item,parts),**box})
                except ValueError: problems.append('Нестандартная диаграмма или неполные исходные данные: макет доступен только для просмотра.')
            if item.object_kind == 'picture' and role and role.role not in ('logo', 'decoration', 'background', 'footer', 'page_number') and box['w'] * box['h'] > width * height * .035:
                pictures.append((item, box))
            if item.object_kind == 'table':
                table=tables.get(item.source_object_id)
                if not table: problems.append('Не удалось сопоставить ячейки таблицы с исходным объектом.')
                else:
                    for slot,frame in table_slots(table,item,box):
                        slots.append(slot)
                        frames[slide.slide_id][slot['key']]=frame.model_dump()
                        probes=fitter.probe(frame)
                        if any(probe.status=='unverifiable' for probe in probes): problems.append('Не удалось проверить шрифт или геометрию ячеек таблицы.')
            if item.rich_text and item.object_kind == 'shape' and item.placeholder_type not in ('dt', 'sldNum'):
                original = text_of(item)
                if is_sample_furniture(original):
                    continue
                if retain_text_artwork(original, role.role if role else None):
                    continue
                if not original.strip() and item.placeholder_type not in ('title', 'ctrTitle', 'body', 'subTitle', 'obj'):
                    continue
                frame = source_text_frame(item, text_geometry)
                frames[slide.slide_id][f's{item.shape_id}']=frame.model_dump()
                probes = fitter.probe(frame)
                if any(probe.status == 'unverifiable' for probe in probes):
                    if any('font' in reason for probe in probes for reason in probe.reason_codes):
                        problems.append('Шрифт текстового поля недоступен или не содержит нужных символов. Установите шрифты шаблона на сервере.')
                    else:
                        problems.append('В текстовом поле есть неподдерживаемые параметры оформления. Макет сохранён для просмотра.')
                style = item.text_style
                size = frame.paragraphs[0].font_size_pt if frame.paragraphs else None
                size = size or (style.font_size_pt if style and style.font_size_pt else 20)
                # Advisory budget only; every generated/edit payload is physically measured again.
                capacity = min(1800, max(1, int(max(1, box['w']-14)/max(1,size*.62)) * max(1, int(max(1,box['h']-7)/(size*1.35)))))
                slot_role = role.role if role and role.role != 'decoration' else 'body'
                slots.append(dict(key=f's{item.shape_id}', shapeId=item.shape_id, role=slot_role, text=original[:1800], maxChars=capacity,
                                  **box, verticalAlign=inherited_vertical_alignment(item), font=frame.paragraphs[0].font_family if frame.paragraphs else 'Arial', size=size,
                                  bold=bool(style and style.bold), color=inherited_text_color(item, parts) or (style.color if style else None) or '#202020', align={'ctr':'center','r':'right','center':'center','right':'right'}.get(item.rich_text.paragraphs[0].alignment if item.rich_text.paragraphs else None,'left')))
        picture = max(pictures, key=lambda p: p[1]['w'] * p[1]['h'], default=None)
        if not slots:
            visible = [item for item in flatten(slide.objects) if not item.hidden]
            image_only = len([item for item in visible if item.object_kind == 'picture']) == 1 and not any(text_of(item).strip() for item in visible)
            problems.append('Слайд сохранён одной картинкой: отдельных редактируемых текстовых полей нет.' if image_only else 'На слайде нет доступных для заполнения текстовых полей.')
        if len(slots)>80: problems.append('На слайде более 80 полей. Разделите слишком плотный макет.')
        layouts.append(dict(id=slide.slide_id, index=slide.slide_index, part=slide.source_part,
                            name=(slots[0]['text'][:90] if slots else '') or f'Слайд {slide.slide_index+1}', slots=slots, charts=charts,
                            **(dict(imageShapeId=picture[0].shape_id, imageBox=picture[1],imageAspectRatio=(picture[0].geometry_full.local_bbox.width_emu/picture[0].geometry_full.local_bbox.height_emu) if picture[0].geometry_full and picture[0].geometry_full.local_bbox and picture[0].geometry_full.local_bbox.height_emu else picture[1]['w']/picture[1]['h']) if picture else {}),
                            visuals=dict(unlabelledShapes=sum(1 for obj in flatten(slide.objects) if not obj.hidden and obj.object_kind in ('shape','connector') and not text_of(obj).strip())),
                            warnings=list(dict.fromkeys(problems)), usable=bool(slots) and not problems and len(slots) <= 80))
        layouts[-1].update(layout_readiness(layouts[-1], frames[slide.slide_id], fitter))
        # A valid package with ordinary editable content can be rebuilt when
        # its original frames cannot be measured. Unsupported native objects
        # remain disabled rather than being silently dropped by reconstruction.
        descendants = list(flatten(slide.objects))
        supported = all(item.object_kind in ('group','shape','picture','table','chart','connector','line') for item in descendants)
        supported = supported and len(charts) == sum(item.object_kind == 'chart' for item in descendants)
        if not layouts[-1]['usable'] and slots and len(slots)<=80 and supported:
            layouts[-1]['requiresRebuild'] = True
            layouts[-1]['usable'] = True
        from runtime.vector_charts import candidates
        graphics=candidates(xml(parts[slide.source_part]),slots)
        if graphics:
            layouts[-1]['vectorCharts']=graphics
            layouts[-1]['requiresRebuild']=True

    warnings = list(dict.fromkeys(p for l in layouts for p in l['warnings']))
    colors=[c.value for c in sorted(model.color_usage_metrics,key=lambda c:c.occurrences,reverse=True) if c.value.startswith('#')][:8]
    style_profile=dict(colors=colors,backgrounds=list(dict.fromkeys(s.background.color for s in model.slides if s.background and s.background.color)),fonts=model.theme.fonts if model.theme else [])
    for layout in layouts:
        layout['drawnTableShapeIds']=[t['shapeId'] for t in drawn_tables if t['part']==layout['part']]
    data = dict(sha256=original_sha, recoveryVersion=RECOVERY_VERSION, width=width, height=height, layouts=layouts, warnings=warnings, parserVersion=model.parser.agent_version,styleProfile=style_profile,
                fontSubstitutions=substitutions, bulletFontSubstitutions=bullet_substitutions, textboxRepairs=textbox_repairs, tableRowRepairs=table_repairs, drawnTableRepairs=drawn_tables, fontReport=font_report(requirements), **(dict(preparedSha256=prepared_sha) if prepared_sha else {}))
    (folder / 'analysis.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    (folder / 'frames.json').write_text(json.dumps(frames,ensure_ascii=False),encoding='utf-8')
    return data

def validate_fields(folder, slides, expansions=None, repair_options=None, repair_keys=None):
    frames = read_frames(folder)
    data = json.loads((folder / 'analysis.json').read_text())
    layouts = {l['id']: l for l in data['layouts']}
    fitter = NativeTextFitter()
    issues = []
    metadata=None
    expansion_context=None
    include_legacy=any(s['native'].get('composition') for s in slides)
    rebuild_profiles=None
    for index, slide in enumerate(slides):
        native = slide['native']
        layout = layouts.get(native['sourceSlideId'])
        if native.get('preserveSource') is True:
            expected={s['key']:s.get('text','') for s in (layout or {}).get('slots',[])}
            if not layout or native.get('mode')!='source' or native.get('fields')!=expected or any(native.get(k) for k in ('rebuild','composition','geometryRepairs','tableRows','charts')) or slide.get('images'):
                raise ValueError('pptx_invalid_source_preservation')
            continue
        if native.get('rebuild'):
            from runtime.rebuild import profiles,validate
            if not layout or native.get('mode')!='rebuild':raise ValueError('pptx_rebuild_source_missing')
            if rebuild_profiles is None:rebuild_profiles=profiles(folder)
            validate_charts(layout,native)
            issues.extend(validate(rebuild_profiles[layout['id']],native,index))
            continue
        # Stored analysis may still contain the old blanket probe-overflow ban.
        # Re-evaluate that gate; the actual payload is always measured below.
        if not layout or (not layout['usable'] and not layout_readiness(layout, frames.get(layout['id'], {}), fitter)['usable']):
            raise ValueError('pptx_layout_unavailable')
        if native.get('mode')=='source' and any(native.get(k) for k in ('composition','tableRowWeights','textSizes')):
            raise ValueError('pptx_source_geometry_changed')
        if native.get('composition'):
            from runtime.layout_metadata import layout_metadata
            from runtime.legacy.composition import validate_composition
            if metadata is None: metadata=layout_metadata(folder,include_legacy=include_legacy)
            issues.extend(validate_composition(metadata[layout['id']].get('adaptive'),native,index))
            continue
        validate_charts(layout,native)
        if metadata is None:
            from runtime.layout_metadata import layout_metadata
            metadata=layout_metadata(folder,include_legacy=include_legacy)
        slide_metadata=metadata[layout['id']]
        layout, slide_frames = expanded_table_layout(layout, frames[layout['id']], native.get('tableRows'),native.get('tableRowWeights'),native.get('tableColumns'))
        slide_frames=apply_table_text_sizes(layout,slide_frames,native.get('textSizes'))
        expected = {s['key'] for s in layout['slots']}
        if set(native['fields']) != expected:
            raise ValueError('pptx_fields_mismatch')
        if any(not isinstance(value,str) or len(value)>4000 for value in native['fields'].values()):
            raise ValueError('pptx_invalid_text')
        if native.get('safeTextExpansion') is True and native.get('mode') == 'source':
            from runtime.safe_text_expansion import ExpansionContext, expand_fields, expanded_layout
            from runtime.geometry_repair import apply_repairs, repair_context
            from runtime.text_containers import normalize_frame_bounds
            if expansion_context is None: expansion_context=ExpansionContext(folder,data)
            slide_frames,bounded=normalize_frame_bounds(layout,slide_frames,expansion_context,native.get('alignmentIntent'),native['fields'])
            layout=expanded_layout(layout,bounded)
            if repair_options is not None:
                repair_options[index]=repair_context(layout,expansion_context,set(repair_keys or native['fields']))
            slide_frames,changes,geometry_issues=apply_repairs(layout,slide_frames,native.get('geometryRepairs'),expansion_context)
            for key,change in changes.items():
                if key in bounded:change['before']=bounded[key]['before']
            changes={**bounded,**changes}
            issues.extend(dict(slide=index,**issue) for issue in geometry_issues)
            layout=expanded_layout(layout,changes)
            # Reconcile saved/model geometry with the AI's current card alignment
            # policy, using the same final bounds for fit, export and render QA.
            slide_frames,aligned=normalize_frame_bounds(layout,slide_frames,expansion_context,native.get('alignmentIntent'),native['fields'])
            for key,change in aligned.items():
                if key in changes:change['before']=changes[key]['before']
            changes.update(aligned)
            layout=expanded_layout(layout,aligned)
            slide_frames,automatic=expand_fields(layout,slide_frames,native['fields'],expansion_context,fitter)
            for key,change in automatic.items():
                if key in changes:change['before']=changes[key]['before']
            changes.update(automatic)
            layout=expanded_layout(layout,automatic)
            from runtime.cell_padding import expand_cell_padding
            slide_frames,padding=expand_cell_padding(layout,slide_frames,native['fields'],fitter)
            changes.update(padding)
            if expansions is not None: expansions[index]=changes
            if changes:
                from runtime.text_regions import text_limits
                from runtime.layout_metadata import picture_text_limits, text_occlusions
                root=expansion_context.roots[layout['id']];items=expansion_context.slides[layout['id']]
                limits=text_limits(layout['slots'],slide_metadata.get('reservedRegions',[]),root)
                limits=picture_text_limits(items,layout['slots'],expansion_context.parts,root,limits)
                slide_metadata={**slide_metadata,'textLimits':limits,'textOcclusions':text_occlusions(items,layout['slots'],expansion_context.parts)}
        issues.extend(dict(slide=index,**issue) for issue in slide_metadata.get('textOcclusions',[])
                      if native.get('fields',{}).get(issue['key'],'').strip() and not (native.get('preserveTemplate') and native['fields'].get(issue['key'])==next((s.get('text') for s in layout['slots'] if s['key']==issue['key']),None)))
        from runtime.photo_safety import replacement_photo_issues
        issues.extend(dict(slide=index, **issue) for issue in replacement_photo_issues(layout, metadata[layout['id']], slide))
        assigned={image.get('shapeId') for image in slide.get('images',[]) if image.get('image')}
        for visual in metadata[layout['id']].get('visualSlots',[]):
            if visual['shapeId'] not in assigned:continue
            for key in (visual.get('detection') or {}).get('markerFieldKeys',[]):
                if native['fields'].get(key,'').strip():
                    issues.append(dict(slide=index,key=key,reason='placeholder_instruction',message='Удали служебную надпись заполнителя изображения. Это не содержательная подпись.'))
        for key, value in native['fields'].items():
            if native.get('preserveTemplate') and value==next((s.get('text') for s in layout['slots'] if s['key']==key),None): continue
            from runtime.text_regions import constrained_frame
            slot=next(s for s in layout['slots'] if s['key']==key)
            limit=slide_metadata.get('textLimits',{}).get(key) if native.get('mode')=='source' else None
            measured_frame=constrained_frame(slide_frames[key],slot,limit,fitter)
            report = fitter.fit(measured_frame, value.split('\n'))
            if report.status != 'fits':
                region_issue=limit and (measured_frame.height_emu < slide_frames[key]['height_emu'] or measured_frame.visible_width_emu is not None) and report.status=='overflow'
                guidance=overflow_guidance(report,value) if report.status=='overflow' else {}
                if region_issue:
                    guidance.update(textRegion=limit,message='Умести текст в указанную свободную область textRegion, сохрани смысл и факты. Не меняй соседние поля. Если explicitLines=true, сократи строки или задай явные переносы через \\n: автоматический перенос исходной рамки может заходить под картинку.')
                issues.append(dict(slide=index, key=key, reason=report.status, details=['source_text_reserved_region'] if region_issue else report.reason_codes,
                                   **guidance))
    # The explicit editor frame/style is applied after the automatic layout.
    # Its actual PDF paint is checked by inspect_render with the same override;
    # the pre-edit template estimate is no longer its geometry contract.
    return [issue for issue in issues if not (
        issue.get('reason') in ('overflow','unverifiable') and isinstance(issue.get('slide'),int)
        and 'native.fields.'+str(issue.get('key')) in slides[issue['slide']].get('textStyles',{}))]
