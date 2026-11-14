# 本地运行与 Dify 接入

## 默认入口

先按 README 运行 CLI。它只需要 Python 与 jieba，不需要启动 Docker、Dify、向量模型或任何付费 API。

查询模式：`raw` 原文候选；`knowledge` Wiki 材料；`assess` 结构化项目判定；`impact` 已建影响关系。`assess` 需要 `--project <JSON文件>`，字段契约见冻结包中 `ontology/model/schema.json`；未知信息不要写成 false。

冻结包：`b-571ebd2d195f4e9a96d965b40372b21d`。Wiki 来源提交 `1661835`，本体来源提交 `f9d4f7e`，不包含两维护方后来工作目录中的全部改动。快照保留既有哈希和来源记录；其中历史工具的本机默认路径只是原始源码记录，不是 README 查询命令的运行前提。

## 可选：复现对话页面

仓库不打包整套 Dify 的镜像与数据库，也不包含 Dify 会话历史。需要自己已有的 Dify 环境；本机验证版本为 1.19.0。

1. 启动本地工具。先自行生成一个至少 32 字符的随机密钥，保存在 Git 已忽略的 `local/adapter.key`；不要提交或放进前端。

   ```bash
   python -B tools/integration176/adapter176.py --store data/bundles --candidate b-571ebd2d195f4e9a96d965b40372b21d --key-file local/adapter.key --bind 127.0.0.1 --port 8100
   ```

2. 在 Dify 导入 `dify/chatflow.yml`。HTTP 节点默认地址 `http://rag-energy176:8080/dify-query` 对应原本的 Docker 服务名；请改成你的 Dify 容器实际能访问到的工具地址。宿主机 `127.0.0.1` 与容器内的 `127.0.0.1` 不是同一地址。若需供容器访问，按自己的网络配置绑定可达地址，并保留密钥鉴权。
3. 将 Dify 环境变量 `ENERGY176_KEY` 设为上述密钥。工作流只保留 Start、模板转换、HTTP、Answer 等节点；无需加入 LLM。
4. 在本地 Dify 发布该对话应用，取得你自己的 WebApp code，将 `web/app.js` 中的 `YOUR_DIFY_APP_CODE` 替换为它。这个 code 是站点标识，不是模型 API Key。
5. 启动前端本地预览，`--upstream` 改成你的 Dify nginx 地址。

   ```bash
   python -B web/serve.py --port 8099 --upstream 127.0.0.1:8088
   ```

6. 浏览器打开 `http://127.0.0.1:8099/`。本地反代支持当前前端所用的 GET、POST、DELETE。

`serve.py` 仅用于本机预览。本仓库没有部署公网服务，不提供“导入后无需任何配置即可在线使用”的承诺。

## 检查

```bash
python -B -m unittest discover -s tests -p "test_*.py" -v
node --check web/app.js
node --check web/markdown.js
```

浏览器回归脚本为 `tests/frontend_checks.py`，需要自行已有的 Playwright Python 包与 Edge 浏览器，不属于默认运行依赖。先启动本地预览服务，再运行：

```bash
python -B tests/frontend_checks.py --base http://127.0.0.1:8099 --label browser-local
```

脚本使用真实前端与模拟 Dify API，检查流式结束、会话隔离、提示保真等行为；不能替代你自己的 Dify 环境联调。
