# 新能源知识库：分布式光伏政策证据工作台

把公开政策原文、带条件的 Wiki 主张和可执行规则连起来，帮助使用者查材料、核对出处，并在字段齐备时运行有限范围的项目规则。

这是个人学习与作品展示项目，当前主要覆盖分布式光伏政策。**仓库提供本地源码、冻结资料与界面截图，不提供持续在线服务，也不承诺后续持续维护。**

![真实本地对话界面](docs/assets/desktop.png)

## 能展示什么

- **查原文**：返回带文档编号和条款定位的候选材料，可回查本地冻结原件。
- **读 Wiki 材料**：保留主张的适用条件、政策时点、事件时点和“官方未明确”等状态。
- **执行规则**：结构化项目字段由本体规则处理；缺字段、未覆盖和冲突分别呈现。
- **查影响关系**：沿已建立的引用和实体关系查看候选影响范围。
- **Dify 对话界面**：本地 Chatflow 调用 HTTP 工具，再以模板展示结果；本版没有启用 LLM 生成节点。

网页对话入口展示材料检索；项目规则判定和影响追溯由命令行提供，不能把两者混写成网页已具备的功能。

## 最快运行：不需要 Dify 或模型 Key

要求 Python 3.10+。本次导出在 Windows / Python 3.14、`jieba 0.42.1` 上验证；其他平台尚未实测。

```bash
git clone https://github.com/1980c-L/new-energy-knowledge-base.git
cd new-energy-knowledge-base
python -m pip install -r requirements.txt
python -B tools/integration176/local176.py --store data/bundles query --question "并网可以附加保证电网安全必要技术要求吗" --mode knowledge --policy-as-of 2025-12-31
```

这条命令在本地读取冻结材料，不请求模型 API。输出包含主张、条件和原文引用；检索命中不等于问题已经得到完整回答。

```bash
# 区分“官方未明确”和“本次没有匹配”
python -B tools/integration176/local176.py --store data/bundles query --question "50千伏接入方案答复时限是多少" --mode knowledge --policy-as-of 2025-12-31

# 查看第五条相关的已建影响关系
python -B tools/integration176/local176.py --store data/bundles query --question "国家第五条变化影响哪些规则" --mode impact --node "ontology:PV-NAT-2025-007-ATTACH#clause=第五条"

# 无模型、无网络的导出运行检查
python -B -m unittest discover -s tests -p "test_*.py" -v
```

## 页面与架构

`前端 → Dify Chatflow → 本地 HTTP 工具 → 原文 / Wiki / 本体规则`

<img src="docs/assets/mobile.png" alt="手机端真实本地界面" width="270"> <img src="docs/assets/mobile-dark.png" alt="手机端深色界面" width="270">

截图来自本地 Dify 真实会话，不是已上线网站。[接入与目录说明](docs/RUNNING.md) · [验证结果与限制](docs/VALIDATION.md)

## 如何阅读源码

| 入口 | 作用 |
|---|---|
| [`web/`](web/) | 零构建 HTML/CSS/JavaScript 前端与本地反代 |
| [`dify/chatflow.yml`](dify/chatflow.yml) | 无模型对话工作流，HTTP 节点调用本地工具 |
| [`tools/integration176/`](tools/integration176/) | 统一查询、版本绑定、原文回查与 HTTP 适配 |
| [`tools/layer34_165/`](tools/layer34_165/) | 本地检索、证据组装与候选版本组件 |
| [`data/bundles/`](data/bundles/) | 冻结的政策材料、Wiki 主张、规则和来源映射 |
| [`tests/`](tests/) | 导出运行检查与浏览器回归脚本 |

## 重要限制

- 资料按冻结版本展示，未联网核实现行政策效力，不用于项目合规决策。
- 网页只展示部分原文节选；命中材料、规则结论、生成答案是不同层次。
- 完全无关的自然语言问题仍可能返回政策候选；本版没有通用的语义拒答保证。
- 云模型生成的答案质量不在本版验收结论中；历史研究结果不能视为当前功能已通过。
- 免费公网部署已经取消。本地运行、源码展示和长期在线服务是不同交付范围。

## AI 辅助开发与项目状态

本项目由使用者组织需求、用途和取舍，CodeBuddy、DSH 与 Codex 辅助实现代码、整理资料及复验。前端属于两边接续修改的同一代码链；本仓库保留复验后的展示版本。

项目以个人学习和阶段性展示为目标。后续若继续维护，建议先处理问题范围识别和证据全文入口，再考虑增加生成能力。

公开政策材料与项目代码的说明见 [NOTICE](NOTICE.md)。
