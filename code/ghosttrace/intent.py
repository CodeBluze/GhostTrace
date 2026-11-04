from __future__ import annotations

import re
import unicodedata


_DELETION_PATTERNS = (
    r"\b(delet\w*|eras\w*|remov\w*|clos\w*)\b.*\b(account|data|profile|information|details|copies|traces)\b",
    r"\b(account|data|profile|information|details|copies|traces)\b.*\b(delet\w*|eras\w*|remov\w*|clos\w*|gone)\b",
    r"\b(right to be forgotten|forget me|wipe my data|terminate my account)\b",
    r"\b(eliminar|eliminaci[oó]n|borrar|suprimir)\b",
    r"\b(supprim\w*|suppression|effacer|droit [aà] l.oubli)\b",
    r"\b(l[oö]schen|entfernen|vergessen)\b",
    r"\b(cancellare|eliminare)\b",
    r"\b(apagar|excluir)\b",
    r"\b(exclus[aã]o|elimina[cç][aã]o)\b",
    r"(削除|消去|탈퇴|삭제|حذف|مسح|удалить|удаление)",
    r"\b(privacy request failed|remaining records cannot identify me|nothing tied to the old address)\b",
)

_NON_DELETION_PATTERNS = (
    r"\b(access request|copy of my data|download my data|data export|export my data)\b",
    r"\b(correct|correction|update|change)\b.*\b(address|email|phone|name|details)\b",
    r"\b(marketing|newsletter|promotional)\b.*\b(opt.?out|unsubscribe|stop)\b",
    r"\b(login|password|sign.?in|locked out|recover)\b",
    r"\b(invoice|receipt|order history|purchase history)\b",
    r"\b(do not|don.t|not asking|not a request to|without)\b.{0,60}\b(delete|deletion|erase|remov\w*)\b",
    r"\b(marketing lists? only|information request)\b",
    r"\b(correct|fix)\b.*\b(surname|spelling|misspell)\b",
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def authorizes_deletion(text: str) -> bool:
    """Conservative multilingual deletion-intent classifier.

    Explicit deletion language wins. Otherwise, common access, correction,
    portability, recovery, and marketing-only requests remain out of scope.
    """

    folded = _fold(text)
    if any(re.search(pattern, folded, re.I) for pattern in _NON_DELETION_PATTERNS):
        return False
    explicit = any(re.search(pattern, folded, re.I) for pattern in _DELETION_PATTERNS)
    if explicit:
        return True
    return False

