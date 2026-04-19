"""Pydantic v1/v2 compatibility layer.

Provides unified interface for both Pydantic v1 and v2.
"""

from __future__ import annotations

try:
    from pydantic import VERSION as PYDANTIC_VERSION
    PYDANTIC_V2 = PYDANTIC_VERSION.startswith("2.")
except ImportError:
    PYDANTIC_V2 = False


if PYDANTIC_V2:
    from pydantic import BaseModel as PydanticBaseModel

    def model_dump(obj: PydanticBaseModel, **kwargs) -> dict:
        """Dump model to dict (Pydantic v2)."""
        return obj.model_dump(**kwargs)

    def model_dump_json(obj: PydanticBaseModel, **kwargs) -> str:
        """Dump model to JSON string (Pydantic v2)."""
        return obj.model_dump_json(**kwargs)

    def model_validate(cls: type[PydanticBaseModel], obj: dict, **kwargs):
        """Validate and parse dict to model (Pydantic v2)."""
        return cls.model_validate(obj, **kwargs)

    def model_json_schema(cls: type[PydanticBaseModel], **kwargs) -> dict:
        """Get JSON schema for model (Pydantic v2)."""
        return cls.model_json_schema(**kwargs)

else:
    from pydantic import BaseModel as PydanticBaseModel

    def model_dump(obj: PydanticBaseModel, **kwargs) -> dict:
        """Dump model to dict (Pydantic v1)."""
        # v1 uses dict()
        exclude = kwargs.pop('exclude', None)
        exclude_unset = kwargs.pop('exclude_unset', False)
        exclude_none = kwargs.pop('exclude_none', False)
        return obj.dict(
            exclude=exclude,
            exclude_unset=exclude_unset,
            exclude_none=exclude_none,
        )

    def model_dump_json(obj: PydanticBaseModel, **kwargs) -> str:
        """Dump model to JSON string (Pydantic v1)."""
        exclude = kwargs.pop('exclude', None)
        exclude_unset = kwargs.pop('exclude_unset', False)
        exclude_none = kwargs.pop('exclude_none', False)
        return obj.json(
            exclude=exclude,
            exclude_unset=exclude_unset,
            exclude_none=exclude_none,
        )

    def model_validate(cls: type[PydanticBaseModel], obj: dict, **kwargs):
        """Validate and parse dict to model (Pydantic v1)."""
        # v1 uses parse_obj
        return cls.parse_obj(obj)

    def model_json_schema(cls: type[PydanticBaseModel], **kwargs) -> dict:
        """Get JSON schema for model (Pydantic v1)."""
        return cls.schema(**kwargs)


__all__ = [
    'PYDANTIC_V2',
    'model_dump',
    'model_dump_json',
    'model_validate',
    'model_json_schema',
]
