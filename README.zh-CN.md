# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS 是以 workspace 产物为事实源的多智能体科研运行时，用于文献检索与阅读、证据约束的
Idea 生成、外部实验交接、论文写作与审阅、真实编译和投稿包生成。系统不依赖模型记忆上一轮对话；
每个阶段都有落盘文件、校验、日志与恢复语义。

```text
T1 研究范围 -> T2 文献 -> T3 阅读 -> T3.5 综合
  -> 可选 T3.6 综述 -> T4 Idea -> T4.5 新颖性
  -> T5 外部执行器交接 -> T7 证据与 Claim
  -> T8 论文 -> T9 投稿包
```

## 运行前须知

- 同一个 workspace 同一时刻只允许一个写入者。不要同时用本地和 Docker 写同一项目。
- API key 等机密只放 `.env`；不要提交 `.env`、workspace、PDF、日志或生成的投稿文件。
- `requirements.txt` / `pyproject.toml` 只管理 Python 依赖。TeX、`latexmk` 与字体由宿主机或 Docker
  镜像提供。

## 本地安装

```bash
git clone <repository-url> DIG-ResearchOS
cd DIG-ResearchOS

conda env create -f environment.yml
conda activate researchos
cp .env.example .env
pip install -e .
```

不用 Conda 时：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

T3.6 综述和 T9 投稿需要真实 PDF 时，安装宿主机 TeX，或使用后文 Docker fallback。Ubuntu/Debian：

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk texlive-latex-base texlive-latex-extra \
  texlive-fonts-recommended texlive-xetex texlive-lang-chinese
```

开始长任务前检查配置、依赖、实际选择的 TeX 后端和 provider：

```bash
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli selftest
```

尚未 editable install 时可从 checkout 运行：

```bash
PYTHONPATH="$PWD" python -m researchos.cli doctor --workspace ./workspace/project-a
```

## Docker Compose

Docker 与本地模式使用同一个 CLI、状态机、validator 和 workspace 格式。宿主机 `workspace/`
挂载为容器内 `/app/workspace`；提供的镜像已包含 matplotlib、TeX Live、`latexmk`、pdfLaTeX、
XeLaTeX、BibTeX 和中文 TeX 支持。

```bash
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml build researchos
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

容器中的示例：

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  init-workspace --workspace /app/workspace/project-a \
  --project-id project-a --topic "面向 LLM Agent 的记忆系统"

docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/project-a
```

本地 `latex.default_backend: auto` 依次使用本机 `latexmk`、本机 `tectonic`、允许的 Docker
TeX 镜像。Compose 不使用 Docker-in-Docker。详见 [docs/docker.md](docs/docker.md)。

## 初始化并运行项目

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "面向 LLM Agent 的记忆系统"

python -m researchos.cli run --workspace ./workspace/project-a
```

`run` 会在重要决策处停在 Human Gate。T2 支持一次自然语言输入阅读覆盖、稿件语言与中文文献策略：

```text
候选 30 篇，精读 15 篇，摘要轻读 15 篇；英文稿，不搜索中文文献。
```

确认后的参数会在真正检索前写入 `literature/literature_params.json`。

## 日常命令

| 目标 | 命令 |
| --- | --- |
| 查看当前阶段与暂停原因 | `python -m researchos.cli status --workspace ./workspace/project-a` |
| 继续暂停项目 | `python -m researchos.cli resume --workspace ./workspace/project-a` |
| 只跑一个 task | `python -m researchos.cli run-task T3.6-SEC-INTRO --workspace ./workspace/project-a` |
| 校验一个 task 的产物 | `python -m researchos.cli validate --task T3.6-SEC-INTRO --workspace ./workspace/project-a` |
| 查看已记录 run | `python -m researchos.cli trace <run-id> --workspace ./workspace/project-a` |
| 检查环境和 TeX | `python -m researchos.cli doctor --workspace ./workspace/project-a` |
| 检查系统配置 | `python -m researchos.cli validate-config` |

跨项目复用已验证上游材料时，创建新的目标 workspace 后使用
`run --from <source-workspace> --start-task <task>`。它不是两个项目的合并操作。恢复细节见
[docs/QUICKSTART.md](docs/QUICKSTART.md)。

综述单节被中断时，应先执行 `validate --task T3.6-SEC-...` 再 `resume`。校验通过的章节会直接
推进，不会再次让模型改写；单节任务只能写自己的 `.tex` 文件和对应的 `survey_state` 条目。

## 引导式 Skill

系统提供可恢复的原子 Skill：PDF/DOI 导入、文献卡、证据矩阵、Idea、论文撰写、审稿、润色、编译
和投稿检查。

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a
```

TTY 终端中的 `run-skill` 会对缺失材料进行多轮收集，只把人工输入或上传内容暂存到
`user_inputs/<skill>/`；重新检查材料后，必须明确输入“执行”或“暂停”才开始 Skill。缺少输入时
不会启动 provider，也不会写论文、实验或引用最终产物。自动化/管道运行请显式加
`--non-interactive`，它会保留 `WAITING_INPUT` 可恢复会话。

目录中展示的每个输入/输出路径都会在 Skill 发现阶段与该 Skill 的读写权限交叉校验，因此不会出现
“界面提示可以使用某文件、运行后却 `access_denied`”的契约漂移。

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01 \
  --resume
```

完整能力与输入契约见 [docs/skills.md](docs/skills.md)。

## 实验协议边界

系统不是按名称禁用 AUUC、Qini、accuracy、F1、某个 benchmark 或 baseline。只要用户材料、
已审计 workspace Artifact 或已验证计划明确给出这些内容并可追溯来源，系统可以使用和审计它们。
禁止的是从研究主题、方法名、学科惯例或示例中把它们凭空固定为“当前项目协议”。缺失时必须标为
`unknown`、`proposed_not_verified` 或等待人工/证据补充。

## CLI 展示与调试

每个实际 CLI 命令默认都会显示一次 `DIG Lab · BUAA` / ResearchOS 启动页。交互终端显示彩色的
`D -> DI -> DIG` 动画；非 TTY 会显示可复制的静态面板。

- `--no-banner`：脚本或 CI 中关闭启动页。
- `--no-color`：关闭 ANSI 颜色但保留同等信息。
- `--verbosity concise|normal|detailed`：控制科研过程展示密度。
- `--quiet`：仅保留关键状态、暂停、错误和最终结果。
- `--json-events`：额外向 stdout 镜像结构化事件；每次运行仍会写
  `<workspace>/_runtime/events/<run-id>.jsonl`。

控制台展示阶段输入、计算、决策、风险和 Artifact Manifest，不会暴露模型私有思维或完整 prompt。
日志和 trace 排障见 [docs/logging.md](docs/logging.md)。

## 文档导航

| 需要了解 | 文档 |
| --- | --- |
| 首次运行与恢复 | [docs/QUICKSTART.md](docs/QUICKSTART.md) |
| 阶段、分支与 Artifact | [docs/agent_pipeline.md](docs/agent_pipeline.md) |
| 配置 | [docs/config.md](docs/config.md) |
| 本地、Docker 与 TeX | [docs/docker.md](docs/docker.md) |
| Runtime、事件与扩展 | [docs/runtime.md](docs/runtime.md) |
| Skills | [docs/skills.md](docs/skills.md) |
| 日志、trace 与排障 | [docs/logging.md](docs/logging.md) |
| 仓库与 workspace 目录 | [docs/project_structure.md](docs/project_structure.md) |
| 开发与测试 | [docs/dev.md](docs/dev.md) |
