"""Read-only JSON chart snapshots that are safe to share across report copies."""

import json
import math


def _read_only(*_args, **_kwargs):
    raise TypeError("report chart series is read-only")


class _ChartDict(dict):
    __slots__ = ()
    __init__ = __setitem__ = __delitem__ = __ior__ = _read_only
    clear = pop = popitem = setdefault = update = _read_only

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self


class _ChartList(list):
    __slots__ = ()
    __init__ = __setitem__ = __delitem__ = __iadd__ = __imul__ = _read_only
    append = clear = extend = insert = pop = remove = reverse = sort = _read_only

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self


def is_frozen_chart_json(value):
    """Recognize only the detached immutable containers produced here."""
    return isinstance(value, (_ChartDict, _ChartList))


def _freeze_json(value):
    if is_frozen_chart_json(value):
        return value
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("chart JSON keys must be strings")
        result = dict.__new__(_ChartDict)
        dict.update(result, {key: _freeze_json(child) for key, child in value.items()})
        return result
    if isinstance(value, list):
        result = list.__new__(_ChartList)
        list.extend(result, (_freeze_json(child) for child in value))
        return result
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("chart snapshot contains a non-JSON value")


def freeze_chart_series(series):
    """Detach a complete JSON curve; ordinary JSON encoders keep its old shape.

    Non-JSON legacy values retain their existing encoder/copy behavior rather
    than making an otherwise readable report fail during this optimization.
    """
    try:
        return _freeze_json(series)
    except TypeError:
        return series


def freeze_report_chart_series(report):
    """Copy only containers leading to curves and intern complete equal curves.

    Business fields stay mutable and all chart metadata and point order survive.
    The source object is never frozen or changed. Interning uses complete JSON
    text, so the same fund's different observation clocks remain distinct.
    """
    canonical, transformed = {}, {}

    def chart(series):
        if isinstance(series, _ChartDict):
            return series
        try:
            key = json.dumps(series, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return series
        if key in canonical:
            # JSON encoders also accept legacy tuples as arrays. They must not
            # acquire the native-JSON container contract of another curve.
            return canonical[key] if canonical[key] == series else series
        frozen = freeze_chart_series(series)
        if isinstance(frozen, _ChartDict):
            canonical[key] = frozen
        return frozen

    def project(node):
        if not isinstance(node, (dict, list)):
            return node
        identity = id(node)
        if identity in transformed:
            return transformed[identity]
        if isinstance(node, list):
            values = [project(child) for child in node]
            result = node if all(old is new for old, new in zip(node, values)) else values
        else:
            values = {
                key: chart(child)
                if key == "series" and isinstance(child, dict) and isinstance(child.get("points"), list)
                else project(child)
                for key, child in node.items()
            }
            result = node if all(node[key] is child for key, child in values.items()) else values
        transformed[identity] = result
        return result

    return project(report)
