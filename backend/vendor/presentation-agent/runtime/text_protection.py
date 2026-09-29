"""Repetition and footer position are not evidence of immutable branding.

Ordinary text must reach the semantic model, even in native footer placeholders.
Keep only unambiguous artwork here; AI decides which textual brands to preserve.
"""
def retain_text_artwork(text, role):
    if role in ('logo', 'page_number'):
        return True
    value = text.strip()
    return len(value) == 1 and not value.isalnum()

def protected_furniture(assignment, item):
    text = '\n'.join(''.join(r.get('text', '') for r in p.get('runs', []))
                     for p in (item.get('rich_text') or {}).get('paragraphs', []))
    return retain_text_artwork(text, assignment.get('role'))
