from __future__ import annotations

"""Pydantic v1 / v2 兼容辅助函数。

当前仓库所在环境并不保证一定是 pydantic v2，因此 runtime 里凡是会跨环境执行的
序列化、schema 导出和模型构造逻辑，都统一走这里的薄兼容层，避免零散地写
`hasattr(model, "model_dump")` 这类判断。
"""

from typing import Any


def model_dump(instance: Any, **kwargs: Any) -> dict[str, Any]:
    """兼容 `BaseModel.model_dump()` / `BaseModel.dict()`。"""

    if hasattr(instance, "model_dump"):
        return instance.model_dump(**kwargs)
    return instance.dict(**_strip_v2_only_kwargs(kwargs))


def model_validate(model_cls: Any, data: Any) -> Any:
    """兼容 `BaseModel.model_validate()` / `BaseModel.parse_obj()`。"""

    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(data)
    return model_cls.parse_obj(data)


def model_json_schema(model_cls: Any, **kwargs: Any) -> dict[str, Any]:
    """兼容 `BaseModel.model_json_schema()` / `BaseModel.schema()`。"""

    if hasattr(model_cls, "model_json_schema"):
        return model_cls.model_json_schema(**kwargs)
    return model_cls.schema(**kwargs)


def _strip_v2_only_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """移除 pydantic v1 不认识的参数。"""

    filtered = dict(kwargs)
    filtered.pop("mode", None)
    return filtered
