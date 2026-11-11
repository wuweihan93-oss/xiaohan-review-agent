# 小晗带你复习

一个用来真正复习学习资料的小型 Agent。资料先被解析、递归切分、向量化并写入 Milvus Lite；复习时，LangChain Agent 通过 MCP 检索你选中的资料，再根据提示词讲解或逐题抽查。开启联网搜索后，还可以通过 Tavily 查询公开网页，并显示可打开的来源链接。配置 `DB_URL` 后，对话记忆通过 LangGraph PostgreSQL Checkpointer 持久化；留空时回退到重启即清空的 `InMemorySaver`。

## 运行

需要 Python 3.11 至 3.13、`uv`、Node.js 20+ 和 npm。项目 `.env` 已复制教程项目的对话模型配置，并配置百炼 Qwen3-VL-Embedding。Embedding 使用百炼多模态 API；如果将 API Key、Base URL 和模型名留空，才会改用本地多语言模型，首次调用时自动下载。`.env.example` 仅供参考，不要用它覆盖已经复制好的 `.env`。

```bash
cd /Users/williams/Conda/xiaohan-review-agent
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8010
```

另开终端：

```bash
cd /Users/williams/Conda/xiaohan-review-agent/frontend
npm install
npm run dev
```

打开 <http://127.0.0.1:5174>。

## Ubuntu 服务器部署

已提供 `compose.yaml`、前后端 Dockerfile 与 Nginx 配置。按 [部署说明](deploy/部署说明.md) 上传部署包和 `.env`，在 Ubuntu 执行 `docker compose up -d --build`。Python 与 Node 依赖会按锁文件重新安装，资料库和上传文件通过 `runtime/` 持久化，对话继续使用现有 PostgreSQL。

首次部署成功后，可按 [CI/CD 配置指南](deploy/CI-CD配置指南.md) 配置 GitHub Actions 和国内镜像仓库。推送 `main` 后自动测试、构建并推送 Linux 镜像，通过 SSH 下发小型部署配置，Ubuntu 从仓库拉取镜像并更新容器；失败时尝试恢复上一版。镜像按内容摘要固定版本，重复部署会复用已有镜像层。模型与数据库密钥继续只放在服务器 `.env`。

## 使用方式

1. 在“学习资料”导入 `langchain_tutorial` 中的教程，或上传自己的 PDF、DOCX、Markdown、TXT、Python 文件和 notebook。
2. 每份资料会单独解析、切分、生成向量并保存到 Milvus Lite，可查看切片数量，也可删除。
3. 在对话顶部展开“资料”下拉，直接搜索和勾选当前会话的资料，支持全选与清空；手机端也不会跳走。资料调整用于下一条消息，会话、已有问答和输入草稿保留。“上传 / 管理资料”入口用于单独管理文件。
4. Agent 会通过 MCP 搜索工具查找选中资料。回答显示引用文件、命中的片段和 Agent 工具调用过程。
5. 左侧“历史复习”按最近复习时间排列。点击记录可恢复问答、引用、资料范围、提示词及复盘/抽查模式，并继续同一个会话；点击“新复习”会创建新会话，旧记录保留。
6. 鼠标移到历史会话上，点击垃圾桶图标并确认即可删除；手机端直接显示删除按钮。删除会话会同时清理问答与 Agent 记忆，保留学习资料。
7. 回答会逐步显示，检索期间可以查看工具过程。点击输入框右下角的停止按钮可中断生成；已有文字会作为中断记录保留。
8. 输入框下方的“联网搜索”默认关闭。开启后，所选资料无法回答当前问题时会查询网页，也可以不选文件直接查询主题；“依据与过程”显示本轮实际使用的工具和网页来源。即使在抽查模式，用户换话题后也会优先回答新问题。
9. AI 回答按 Markdown 显示，支持标题、加粗、列表、引用、表格和代码高亮，流式生成时也会实时排版。代码块和宽表格在手机上可横向滑动。每条回答下方的“复制”会复制完整 Markdown 原文，保留代码缩进；成功后显示“已复制”。普通 HTTP 部署使用兼容复制方式，浏览器禁止复制时会显示手动复制提示。

## 联网搜索

在本机和 Ubuntu 项目目录的 `.env` 中配置独立的 Tavily Key：

```env
TAVILY_API_KEY=你的Tavily密钥
```

本机重启后端后生效。Ubuntu 修改 `.env` 后，在项目目录执行以下命令，让后端容器重新读取环境变量：

```bash
docker compose up -d --no-deps --no-build --force-recreate backend
```

已有本机配置可以继续使用；模型 Key 和百炼 Embedding Key 不能代替搜索 Key。没有配置时，“联网搜索”不可用，学习资料复习仍可正常使用。Key 通过运行时环境传给 MCP 子进程，不会发到前端或写进镜像。

关闭开关时，本轮不注册或提供 `search_web` 工具，也不把历史网页引用作为本轮新检索结果。开启后，按下面的规则选择来源：

- 选了资料：先通过 `search_study_materials` 检索；资料为空或检索失败时，由 `RetrievalRoutingMiddleware` 强制下一次模型调用选择 `search_web`。
- 返回了片段：模型结合本轮问题判断片段是否能回答，不能只看是否非空或相似度高。片段不相关或内容不足时继续调用 `search_web`；足够回答时可直接基于资料作答。
- 没选资料：直接使用 `search_web`。需要最新信息、版本变化或用户明确要求上网时，也要查询网页。

所选资料是优先参考来源，不限制用户能问的话题。例如选着 LangChain 笔记问游戏玩法，联网开启时应搜索并回答玩法；不会因为笔记没有游戏内容就拒答，也不会强制继续旧的抽查。当前模式与检索规则会覆盖旧自定义提示词和历史拒答中遗留的资料范围限制，无需删除原会话。

搜索一次最多取 5 条网页摘要，不抓取网页全文，不自动将网页写入 Milvus。网页引用展示为链接，资料引用仍跳转资料库；网页不显示向量相似度。搜索超时、认证错误和额度不足会产生中文错误，工具失败仍保留配对的 `ToolMessage`，允许 Agent 围绕已有资料继续讲解，并说明本轮未能核实网上信息。

已有 PostgreSQL 会在启动时按需为会话表补充 `web_search_enabled` 列，旧会话默认关闭联网。网页来源与工具过程沿用现有 JSONB 字段保存，恢复历史时也会恢复该会话的联网开关。

开发时可以沿着下面的调用链复盘：

```text
Vue 联网开关 → POST /api/review/stream → agent.review_stream
    → MultiServerMCPClient → search_web MCP 工具
    → web_search.py 调用 Tavily → ToolMessage 返回网页摘要
    → 模型整理回答 → PostgreSQL 保存引用 → Vue 展示网页链接
```

## 对话记录与记忆

`DB_URL` 指向同一套 PostgreSQL。服务启动时自动创建 `xiaohan_conversations`（标题、资料范围、模式和时间）及 `xiaohan_messages`（逐条问答、引用和工具过程）。LangGraph 自身的 checkpoint 表保存 Agent 的上下文状态。会话 ID 同时关联页面历史与 Agent 记忆，刷新页面或重启后端后仍能继续复习。新会话会在发出第一条问题时保存；生成失败的问题与错误提示也会保留。

旧版 PostgreSQL checkpoint 中能通过小晗专用检索工具确认归属的对话，会在启动时补入历史列表。早期未保存的模式和提示词采用默认值，资料范围从引用片段还原，可在继续复习前调整。数据库中的其他教程 Agent 对话不会混入列表。未配置 `DB_URL` 时仍能使用内存 Agent，但不启用服务端历史记录。

这是供本地单人使用的学习项目，历史列表展示本项目保存的全部会话。

### 流式回答与删除会话

页面通过 `fetch` 发起 `POST /api/review/stream`，后端使用 LangGraph `astream` 的 `messages` 模式取得模型片段，使用 `values` 模式整理本轮引用和工具过程，再通过 SSE 发送给浏览器。`token` 事件追加文字，`state` 更新引用与工具过程，`reset` 清除检索前的过渡语，`done` 返回最终保存的回答；发生错误则发送 `error`。原来的 `POST /api/review` 仍返回完整 JSON，便于调试和对照学习。

同一轮只保存一条用户消息和一条回答，不按 token 写数据库。主动停止或浏览器断开连接时，后端会取消模型生成并保存已有文字；异常结束会保留错误说明。页面跟随回答滚动，上滑查看旧消息时暂停自动滚动。部署用 Nginx 已关闭代理缓冲；如果额外增加一层反向代理，也需要关闭该层的 SSE 缓冲。

`DELETE /api/conversations/{conversation_id}` 在同一个会话锁内先调用 Checkpointer 的 `adelete_thread`，再删除会话表记录，由外键级联删除消息。这样旧 checkpoint 不会在下次启动时重新补入历史列表。生成过程中删除会返回中文忙碌提示，停止生成后即可操作。

在 `backend` 目录运行 `XIAOHAN_POSTGRES_TESTS=1 uv run python -m unittest discover -s tests -v`，可验证历史记录跨连接保存、会话分页与隔离、失败记录保留以及本轮引用筛选。测试只清理自己创建的临时会话。

资料工具出错时，`ToolRecoveryMiddleware` 会返回与 `tool_call_id` 对应的错误消息。若服务中断留下了不完整的旧工具记录，下次调用模型前会补齐中断标记，保留问答和已有检索结果，避免模型因缺失工具返回而拒绝整段对话。

## 跟着代码复盘

- `backend/app/materials.py`：文件解析、`RecursiveCharacterTextSplitter`、百炼 Qwen3-VL-Embedding（未配置时本地回退）和 Milvus 入库/检索。
- `backend/app/agent.py`：LangChain `create_agent`、MCP Adapter、系统 Prompt、`astream` 和 PostgreSQL 持久化 Checkpointer。
- `backend/app/conversations.py`：PostgreSQL 会话/消息表、历史分页查询和旧 checkpoint 记录恢复。
- `backend/app/middleware.py`：LangChain AgentMiddleware、本轮工具选择与联网回退、工具错误处理和中断后的消息修复。
- `backend/app/mcp_server.py`：FastMCP 工具如何把资料检索能力提供给 Agent。
- `backend/app/web_search.py`：Tavily 搜索、网页引用格式、请求超时和中文错误处理。
- `frontend/src/sources.js`：网页引用链接校验与资料相似度显示。
- `frontend/src/stream.js`：SSE 网络拆包解析与中文字符解码。
- `frontend/src/AssistantAnswer.vue`、`markdown.js` 和 `clipboard.js`：AI 回答的 Markdown 排版、代码高亮与剪贴板复制。Markdown 禁用原始 HTML，链接只允许安全的 HTTP / HTTPS 地址。
- `backend/app/main.py`：FastAPI 上传/导入/删除资料和聊天接口。
- `frontend/src/App.vue`：资料导入和复习对话交互。

教程目录只用于第一次导入；导入后资料在本项目的 Milvus Lite 数据库中管理，上传文件保存在本项目的 `backend/uploads/`。删除项目资料不会删除你的原教程文件。
