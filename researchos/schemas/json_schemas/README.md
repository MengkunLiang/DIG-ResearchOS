本目录存放 ResearchOS artifact 的 JSON Schema 文件。

命名约定：
- 文件名：`<schema_name>.schema.json`
- Python 侧通过 `researchos.schemas.validator.validate_against_schema(..., schema_name)` 加载

当前 runtime 已提供通用加载与校验逻辑，具体业务 schema 会随着后续 T-stage agent 落地逐步补齐。
