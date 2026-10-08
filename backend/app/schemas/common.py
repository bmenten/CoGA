"""Shared API types: identifiers, the document base model, gene locations."""

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import core_schema


class ApiId(str):
    """Storage-agnostic API identifier supporting UUID and synthetic string ids."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        return core_schema.no_info_after_validator_function(
            cls.validate,
            core_schema.union_schema(
                [
                    core_schema.is_instance_schema(UUID),
                    core_schema.str_schema(),
                ]
            ),
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema_, handler):
        return {"type": "string"}

    @classmethod
    def validate(cls, value: Any) -> str:
        return str(value)


class ApiDocumentModel(BaseModel):
    id: ApiId = Field(alias="_id")

    model_config = ConfigDict(populate_by_name=True)


class GeneLocation(BaseModel):
    gene: str
    chr: str
    start: int
    end: int
    # The assembly these coordinates belong to. A panel region is stored per assembly and
    # a family's filter reads only its own assembly's (#515); None where no assembly applies.
    assembly_id: Optional[str] = None
    assembly: Optional[str] = None
