# 开发、回归与发布检查

> [中文](../cn/dev.md) | [English](../en/dev.md)

## 本地设置

```bash
conda env create -f environment.yml
conda activate researchos
pip install -e .
python -m researchos.cli validate-config
```

使用 `.env` 存放本地提供者凭据。切勿将其提交到版本库。从仓库根目录运行命令；在可编辑安装之前，使用 `PYTHONPATH="$PWD" python -m researchos.cli ...`。

## 变更规范

1. 编辑前阅读相关阶段、工具、模式、提示和验证器。
2. 保留工作区制品模式和状态机语义，除非变更显式更新了所有使用者。
3. 将控制台可观察性保留在通用渲染器/报告器中，而不是添加临时的 `print` 语句。
4. 不要将领域惯例变成项目协议的默认值。具体的指标/数据集/基线/种子需要有源码绑定的当前项目证据。
5. 对于任何 CLI、环境、制品、恢复或行为更改，更新面向用户的 README 和 `docs/` 中的受影响文档。
6. 对于公共集成技能，在 `SKILL.md` 中声明经过验证的 `workflow`，在阶段边界调用 `update_skill_workflow`，并将每个建议的制品保持在声明的工作区策略内。

## 验证

```bash
PYTHONPATH=. python -m compileall -q researchos
PYTHONPATH=. python -m researchos.cli validate-config --no-banner --no-color
pytest -q tests/unit
pytest -q tests/real
git diff --check
```

为更改的逻辑添加针对性测试。至少，行为变更应覆盖其验证器/工具/CLI 路径，以及一个真实或快照式集成路径，其中影响范围触及用户可见的运行时行为。

对于上下文自适应批处理，测试多篇论文的提供者上下文批次以及格式错误/部分批次的回退。断言独立的笔记制品和 `ABSTRACT_ONLY` 边界；永远不要只测试提供者调用次数。

根据仓库策略，本次检出中本地 `tests/` 目录被忽略；在本地运行测试，但不要仅仅为了使更改看起来已测试而意外暂存被忽略的测试固定件。

## 有用的命令

```bash
python -m researchos.cli doctor --workspace /tmp/researchos-dev
python -m researchos.cli run-task HELLO --workspace /tmp/researchos-dev
python -m researchos.cli list-skills --workspace /tmp/researchos-dev
python -m researchos.cli describe-skill domain-synthesis-studio --workspace /tmp/researchos-dev
python -m researchos.cli validate --task T3.6-SEC-INTRO --workspace ./workspace/project-a
```

对于 Compose 回归：

```bash
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml build researchos
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

Compose 是一个普通的执行环境，而非 Docker-in-Docker。不要仅仅为了使 LaTeX 工作而将 Docker 套接字挂载到运行时中。

## 文档与发布检查清单

- 验证配置和所有受影响的测试。
- 对原生/Docker/TeX 更改运行 `doctor`。
- 当图形更改时，使用渲染器检查生成的 Survey PDF；检查非空白像素、可读的排版以及事实来源基础。
- 当控制台行为更改时，运行 TTY 或无颜色 CLI 冒烟测试。
- 确认 `git diff --check`、`git status --short` 和暂存文件列表。
- 不要提交 `.env`、工作区输出、PDF、日志、被忽略的本地笔记或不相关的用户更改。
