"""Resolve user edit permissions before classifying any source-deck content."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.presentation.template.models import TemplateAnalysisContext
from app.presentation.template.observations import ElementObservation
from app.presentation.template.work import call_provider


class EditScopeError(ValueError):
    def __init__(self, message: str, *, code: str = "TEMPLATE_EDIT_SCOPE_NEEDS_INPUT"):
        super().__init__(message)
        self.code = code


class EditTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slide_id: str
    element_id: str


class UserEditScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["unrestricted", "scoped", "ambiguous"]
    targets: list[EditTarget] = Field(default_factory=list)
    user_evidence: str
    reason: str


# This intentionally accepts complete, narrow commands, not a keyword found in
# a larger request. More complex commands go through the structured resolver.
_FIRST_TITLE = re.compile(
    r'(?:замени(?:ть)?|измени(?:ть)?)\s+только\s+'
    r'(?:первый\s+заголовок|заголовок\s+(?:на\s+)?(?:первом|1-м|1)\s+слайде)'
    r'(?:\s+на\s+[«"].*?[»"])?\s*[.;,]?\s*'
    r'(?:(?:вс[её]\s+)?остальн(?:ое|ые\s+элементы|ые\s+слайды)\s+'
    r'(?:сохрани(?:ть)?|не\s+меняй|не\s+менять|оставь\s+без\s+изменений|keep))?[.!]?'
    r'|(?:replace|change)\s+only\s+(?:the\s+)?(?:first\s+title|title\s+on\s+(?:the\s+)?first\s+slide)'
    r'(?:\s+(?:with|to)\s+".*?")?\s*[.;,]?\s*(?:keep\s+(?:everything\s+else|the\s+rest)(?:\s+unchanged)?)?[.!]?',
    re.IGNORECASE,
)
_RESTRICTED = re.compile(
    r"(?:замен\w*|измен\w*|редактир\w*|replace|change|edit)\s+(?:\w+\s+){0,3}(?:только|исключительно|only)\b"
    r"|(?:только|исключительно|only)\s+(?:замен\w*|измен\w*|редактир\w*|replace|change|edit)"
    r"|keep\s+(?:the\s+)?(?:rest|everything|(?:all|other)\s+(?:slides|elements|content|text|photos))\b"
    r"|остальн\w*(?:\s+\w+){0,3}\s+(?:сохран\w*|не\s+(?:меня\w*|трога\w*)|keep)"
    r"|ничего\s+не\s+меня\w*"
    r"|leave\s+(?:the\s+)?(?:other|remaining)\s+(?:slides|elements|content)\s+(?:intact|unchanged)",
    re.IGNORECASE,
)


def resolve_edit_scope(context: TemplateAnalysisContext, observations: dict[str, list[ElementObservation]], provider):
    if context.editable_elements is not None:
        for slide_id, ids in context.editable_elements.items():
            known = {value for item in observations.get(slide_id, []) for value in (item.element_id, item.source_object_id)}
            if slide_id not in observations or set(ids) - known:
                raise EditScopeError("An explicit edit target could not be located in the source inventory.")
        return context
    prompt = (context.user_request_summary or "").strip()
    answer = (context.edit_scope_clarification or "").strip()
    if not prompt and not answer:
        return context
    if answer:
        prompt = f"{prompt}\nУточнение пользователя: {answer}"
    normalized = " ".join((answer or prompt).split())
    if _FIRST_TITLE.fullmatch(normalized):
        slides = sorted(observations.values(), key=lambda items: min((item.slide_index for item in items), default=0))
        candidates = [item for item in (slides[0] if slides else [])
                      if item.source_level == "slide" and item.role in {"title", "section_title"}]
        if len(candidates) != 1:
            raise EditScopeError("Cannot uniquely identify the first slide title; the edit scope needs clarification.")
        target = candidates[0]
        return context.model_copy(update={"editable_elements": {target.slide_id: [target.element_id]}})
    if normalized.casefold().rstrip('.!') in {
        "ничего не менять", "всё сохранить", "все сохранить", "keep everything unchanged", "keep all",
    }:
        return context.model_copy(update={"editable_elements": {}})
    # Permissions are relevant only when the user restricts source-content edits.
    # General generation, style references and material requests belong elsewhere.
    if not _RESTRICTED.search(prompt):
        return context
    method = getattr(provider, "resolve_edit_scope", None)
    if context.semantic_mode == "off" or not provider.available or not callable(method):
        if _RESTRICTED.search(prompt):
            raise EditScopeError("Restricted edit request could not be resolved without a semantic provider.", code="TEMPLATE_EDIT_SCOPE_PROVIDER_UNAVAILABLE")
        return context
    inventory = [
        {"slide_id": item.slide_id, "slide_index": item.slide_index, "element_id": item.element_id,
         "role": item.role, "source_level": item.source_level, "source_text": item.text}
        for items in observations.values() for item in items
    ]
    try:
        scope = UserEditScope.model_validate(call_provider(provider, method, user_request=prompt, inventory=inventory))
    except Exception as exc:
        raise EditScopeError("User edit scope resolution failed; no source changes are authorized.", code="TEMPLATE_EDIT_SCOPE_PROVIDER_FAILED") from exc
    if scope.status == "ambiguous":
        raise EditScopeError("Уточните, какой элемент нужно изменить и что следует сохранить. Можно описать его словами.")
    if scope.status == "unrestricted":
        if scope.targets or _RESTRICTED.search(prompt):
            raise EditScopeError("The resolver did not honor a user preservation constraint; clarify the edit scope.")
        return context
    if not scope.user_evidence.strip() or scope.user_evidence not in prompt:
        raise EditScopeError("Edit scope must cite the user's request, not source-deck instructions.")
    allowed = {(item.slide_id, item.element_id) for items in observations.values() for item in items if item.source_level == "slide"}
    resolved: dict[str, list[str]] = {}
    for target in scope.targets:
        if (target.slide_id, target.element_id) not in allowed:
            raise EditScopeError("Edit scope contains an unknown or inherited source target.")
        resolved.setdefault(target.slide_id, []).append(target.element_id)
    return context.model_copy(update={"editable_elements": resolved})
