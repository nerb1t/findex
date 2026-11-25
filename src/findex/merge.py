"""Операції над відсортованими списками doc_id (з Lab 2).

merge - два вказівники. Обидва списки відсортовані, йдемо по них одночасно,
завжди рухаємо той вказівник, де число менше. O(len(a) + len(b)).
set - простіше, але треба побудувати хеш-таблицю і потім ще відсортувати результат.
"""


def merge_and(a, b):
    i = j = 0
    out = []
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    return out


def merge_or(a, b):
    i = j = 0
    out = []
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            out.append(a[i])
            i += 1
        else:
            out.append(b[j])
            j += 1
    # хвіст, який залишився (тільки один з них не порожній)
    out.extend(a[i:])
    out.extend(b[j:])
    return out


def merge_not(a, b):
    """a AND NOT b: все з a, чого немає в b."""
    i = j = 0
    out = []
    while i < len(a):
        if j >= len(b) or a[i] < b[j]:
            out.append(a[i])
            i += 1
        elif a[i] == b[j]:
            i += 1
            j += 1
        else:
            j += 1
    return out


def set_and(a, b):
    return sorted(set(a) & set(b))


def set_or(a, b):
    return sorted(set(a) | set(b))


def set_not(a, b):
    return sorted(set(a) - set(b))


ENGINES = {
    "merge": (merge_and, merge_or, merge_not),
    "set": (set_and, set_or, set_not),
}
