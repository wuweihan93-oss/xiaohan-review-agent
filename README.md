# 小晗带你复习

一个用来真正复习学习资料的小型 Agent。资料先被解析、递归切分、向量化并写入 Milvus Lite；复习时，LangChain Agent 通过 MCP 检索你选中的资料，再根据提示词讲解或逐题抽查。配置 `DB_URL` 后，对话记忆通过 LangGraph PostgreSQL Checkpointer 持久化；留空时回退到重启即清空的 `InMemorySaver`。

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

首次部署成功后，可按 [CI/CD 配置指南](deploy/CI-CD配置指南.md) 配置 GitHub Actions。推送 `main` 后自动测试、构建 Linux 镜像，并通过 SSH 传到服务器更新容器；部署失败时尝试恢复上一版。模型与数据库密钥继续只放在服务器 `.env`。

## 使用方式

1. 在“学习资料”导入 `langchain_tutorial` 中的教程，或上传自己的 PDF、DOCX、Markdown、TXT、Python 文件和 notebook。
2. 每份资料会单独解析、切分、生成向量并保存到 Milvus Lite，可查看切片数量，也可删除。
3. 在“复盘学习”勾选资料，选择“带我复盘”或“抽查我”，和小晗围绕资料对话。
4. Agent 会通过 MCP 搜索工具查找选中资料。回答显示引用文件、命中的片段和 Agent 工具调用过程。
5. 左侧“历史复习”按最近复习时间排列。点击记录可恢复问答、引用、资料范围、提示词及复盘/抽查模式，并继续同一个会话；点击“新复习”会创建新会话，旧记录保留。

## 对话记录与记忆

`DB_URL` 指向同一套 PostgreSQL。服务启动时自动创建 `xiaohan_conversations`（标题、资料范围、模式和时间）及 `xiaohan_messages`（逐条问答、引用和工具过程）。LangGraph 自身的 checkpoint 表保存 Agent 的上下文状态。会话 ID 同时关联页面历史与 Agent 记忆，刷新页面或重启后端后仍能继续复习。新会话会在发出第一条问题时保存；生成失败的问题与错误提示也会保留。

旧版 PostgreSQL checkpoint 中能通过小晗专用检索工具确认归属的对话，会在启动时补入历史列表。早期未保存的模式和提示词采用默认值，资料范围从引用片段还原，可在继续复习前调整。数据库中的其他教程 Agent 对话不会混入列表。未配置 `DB_URL` 时仍能使用内存 Agent，但不启用服务端历史记录。

这是供本地单人使用的学习项目，历史列表展示本项目保存的全部会话。

在 `backend` 目录运行 `XIAOHAN_POSTGRES_TESTS=1 uv run python -m unittest discover -s tests -v`，可验证历史记录跨连接保存、会话分页与隔离、失败记录保留以及本轮引用筛选。测试只清理自己创建的临时会话。

资料工具出错时，`ToolRecoveryMiddleware` 会返回与 `tool_call_id` 对应的错误消息。若服务中断留下了不完整的旧工具记录，下次调用模型前会补齐中断标记，保留问答和已有检索结果，避免模型因缺失工具返回而拒绝整段对话。

## 跟着代码复盘

- `backend/app/materials.py`：文件解析、`RecursiveCharacterTextSplitter`、百炼 Qwen3-VL-Embedding（未配置时本地回退）和 Milvus 入库/检索。
- `backend/app/agent.py`：LangChain `create_agent`、MCP Adapter、系统 Prompt 和 PostgreSQL 持久化 Checkpointer。
- `backend/app/conversations.py`：PostgreSQL 会话/消息表、历史分页查询和旧 checkpoint 记录恢复。
- `backend/app/middleware.py`：LangChain AgentMiddleware、工具错误处理与中断后的消息修复。
- `backend/app/mcp_server.py`：FastMCP 工具如何把资料检索能力提供给 Agent。
- `backend/app/main.py`：FastAPI 上传/导入/删除资料和聊天接口。
- `frontend/src/App.vue`：资料导入和复习对话交互。

教程目录只用于第一次导入；导入后资料在本项目的 Milvus Lite 数据库中管理，上传文件保存在本项目的 `backend/uploads/`。删除项目资料不会删除你的原教程文件。
