"""Paragraph selection shared by source-style measurement and OOXML patching."""


def replacement_paragraph_sources(has_text: list[bool], desired: list[str]) -> list[int]:
    if not has_text:
        raise ValueError("Text replacement requires an observed source paragraph")
    content = [index for index, present in enumerate(has_text) if present]
    # A compact replacement has no spacer paragraphs. Consume the observed
    # content styles in order: heading, item 1, item 2, ... . Picking the nearest
    # source for a blank spacer would duplicate the heading style on item 1.
    if content and desired and all(text.strip() for text in desired):
        return [content[min(index, len(content)-1)] for index in range(len(desired))]
    selected = []
    for index, text in enumerate(desired or [""]):
        if index >= len(has_text):
            selected.append(content[-1] if content else len(has_text) - 1)
        elif text.strip() and not has_text[index] and content:
            selected.append(min(content, key=lambda candidate: (abs(candidate - index), candidate > index)))
        else:
            selected.append(index)
    return selected
