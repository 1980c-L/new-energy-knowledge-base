# 本地运行与 Dify 接入

顺序：命令行 → 网页（可选）→ 版本身份。

## 第一步：命令行

只需要 Python 和 jieba，不用 Docker、不用 Dify、不用向量模型，也不用任何付费 API。

```bash
python -m pip install -r requirements.txt
python -B tools/integration176/local176.py --store data/bundles query --question "并网可以附加保证电网安全必要技术要求吗" --mode knowledge --policy-as-of 2025-12-31
```

四种查询模式：

| 模式 | 给你什么 |
|---|---|
| `raw` | 原文候选片段 |
| `knowledge` | 提炼过的 Wiki 主张及其适用条件 |
| `assess` | 按规则判定一个具体项目 |
| `impact` | 已建立的影响关系 |

`assess` 需要 `--project <JSON文件>`，字段格式见冻结包里的 `ontology/model/schema.json`；不知道的字段请留空，不要填 false——两者含义不同。

## 第二步（可选）：把网页对话跑起来

仓库不打包整套 Dify 的镜像和数据库，也不含 Dify 会话历史。你需要一个自己的 Dify 环境；本机验证版本 1.19.0。

1. **启动本地工具**。先生成一个至少 32 字符的随机密钥，存到 Git 已忽略的 `local/adapter.key`（不要提交，也不要放进前端）。

   ```bash
   python -B tools/integration176/adapter176.py --store data/bundles --candidate b-571ebd2d195f4e9a96d965b40372b21d --key-file local/adapter.key --bind 127.0.0.1 --port 8100
   ```

2. **在 Dify 里导入** `dify/chatflow.yml`。HTTP 节点默认地址是 `http://rag-energy176:8080/dify-query`，那是原来那套 Docker 的服务名，请改成你自己的 Dify 容器能访问到的地址。注意宿主机的 `127.0.0.1` 和容器内的 `127.0.0.1` 不是同一个地址。要让容器访问，就按你的网络情况绑定一个可达地址，并保留密钥鉴权。

3. **设置 Dify 环境变量** `ENERGY176_KEY` 为上一步的密钥。工作流只需要 Start、模板转换、HTTP、Answer 这几类节点，不需要 LLM 节点。

4. **发布对话应用**，拿到你自己的 WebApp code，替换 `web/app.js` 里的 `YOUR_DIFY_APP_CODE`。这个 code 是站点标识，不是模型 API Key。

5. **启动前端本地预览**，把 `--upstream` 换成你的 Dify nginx 地址。

   ```bash
   python -B web/serve.py --port 8099 --upstream 127.0.0.1:8088
   ```

6. 浏览器打开 `http://127.0.0.1:8099/`。本地小代理支持当前前端用到的 GET、POST、DELETE。

`serve.py` 只用于本机预览。仓库没有部署公网服务，「导入就能在线用」这件事在这里不成立。

## 第三步：运行检查

```bash
python -B -m unittest discover -s tests -p "test_*.py" -v
node --check web/app.js
node --check web/markdown.js
```

网页自动检查脚本是 `tests/frontend_checks.py`，需要自备 Playwright Python 包和 Edge 浏览器，不属于默认依赖。先起本地预览，再运行：

```bash
python -B tests/frontend_checks.py --base http://127.0.0.1:8099 --label browser-local
```

## 附录：版本身份

- 冻结包：`b-571ebd2d195f4e9a96d965b40372b21d`
- Wiki 来源提交 `1661835`，本体来源提交 `f9d4f7e`；不含两维护方后来工作目录中的全部改动。
- 快照保留既有哈希与来源记录。其中历史工具里的本机默认路径只是原始源码记录，不是上面查询命令的运行前提。
