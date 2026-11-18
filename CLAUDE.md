# CLAUDE.md

本文件为 Claude Code(claude.ai/code)在此仓库中工作时提供指导。

## 项目概述

基于 LangGraph 的 AI 求职助手:Python 后端(`job_agent` 包,提供 FastAPI 聊天 API 与 LangGraph Agent Server)+ Vue 3 + Vite 前端(`frontend/`)。代码注释中英文混合。

## 开发环境

宿主机 Python(3.12)没有安装项目依赖,所有后端命令都通过已运行的 dev 容器执行(`compose.yaml` 中 `dev` 服务,挂载 `.:/app`,加载 `.env`):

- 宿主机 8000 端口由 Docker 转发到容器内的 uvicorn(`uvicorn job_agent.api.app:app --host 0.0.0.0 --port 8000`,通过 `docker compose exec dev uvicorn ...` 启动,**无 --reload**;修改后端代码后必须在容器内重启 uvicorn)。
- `compose.yaml` 已新增由 Compose 管理的 `api` 服务(uvicorn,宿主端口经 `JOB_AGENT_API_HOST_PORT` 默认 18000,自带 `/health` 健康检查,数据在命名卷 `agent-data`);**在线版本仍是 dev 容器内手工 uvicorn**,切换为独立发布任务(README 有五步 runbook,备份/恢复已用临时 SQLite 假数据演练,尚未执行)。隔离验收命令必须始终带 `--env-file deploy/accept.env -p m0r3-accept -f compose.yaml -f deploy/compose.accept.yaml` 并显式点名 `api`(README 有完整命令块;`--env-file` 把 Compose 插值来源也切离真实 `.env`)。
- `langgraph dev` Agent Server 运行在容器内,端口 2024。
- 容器内运行 `scripts/` 下的脚本必须用 `python -m scripts.xxx`(直接 `python scripts/xxx.py` 会把 `scripts/` 当作 sys.path[0],导致 `import job_agent` 失败)。

## 常用命令

后端(一律 `docker compose exec -T dev <命令>`):

- **跑测试:** `python -m pytest` 直接运行(带覆盖率报告,无门禁);≥90% 门禁只作为 CI 的显式参数 `--cov-fail-under=90`。测试子集(`python -m pytest tests/unit/test_config.py` 或 `-k name`)可直跑,不再需要 `--no-cov`。`tests/conftest.py` 的会话级 autouse 夹具把检查点/记忆/会话默认路径全部重定向到临时目录,从仓库根目录跑测试不会触碰 `data/`。
- **Lint/格式化:** `ruff check .` 和 `ruff format .` —— CI 会执行 `ruff format --check .`,提交前必须先格式化。
- **模型流式能力检查:** `python -m scripts.check_model_stream`(直接对模型服务端验证逐 chunk 流式与首 token 延迟)。
- **API 层 SSE 计时:** `python -m scripts.check_live_stream`(打 `http://localhost:8000`,按到达时间打印 token 事件)。
- **浏览器链路(经 Vite 代理):** 宿主机 `node scripts/check_vite_proxy_stream.mjs`(Vite 只监听 IPv6 回环时用 `"http://[::1]:5173"`)。
- 其余冒烟脚本(`check_llm_connection`、`check_llm_tool_calling`、`check_conversation_memory`、`check_long_term_memory`、`check_memory_approval`、`check_agent_long_term_memory`)均访问真实 LLM,需要模型配置。交互式 CLI:`python -m scripts.chat`。

前端(宿主机,目录 `frontend/`):

- `npm.cmd run dev` 启动 Vite;`npm.cmd run build` 构建;`npm.cmd run test` 运行 vitest(SSE 解析与 useChat 测试)。
- Vite 将 `/api` 代理到 `http://127.0.0.1:8000`(剥掉 `/api` 前缀);代理目标经 `VITE_API_PROXY_TARGET` 可配(默认保持 8000 以兼容在线环境,全新安装按 `frontend/.env.example` 指向 18000);前端需要 `frontend/.env.local` 中的 `VITE_API_KEY`。Vite dev server 只监听 IPv6 回环(`[::1]`)。

CI(`.github/workflows/test.yml`):后端 job 构建镜像并跑 `ruff check .`、`ruff format --check .`、`python -m pytest --cov-fail-under=90`,默认**离线**执行(不注入模型/付费服务/追踪凭据);前端 job 用 Node 24(与本地验证过的 24.21.0 / npm 11 / lockfileVersion 3 一致)跑 `npm ci`、`npm test`(默认 Vitest 池)、`npm run build`;LangSmith 追踪是独立 opt-in job,仅在仓库配置了 `LANGSMITH_API_KEY` 时运行。

## 配置

配置来自 `job_agent/config.py`(pydantic-settings):环境变量前缀 `JOB_AGENT_`,读取仓库根目录 `.env`,`extra="ignore"`。任何真实 LLM 调用必需:`JOB_AGENT_MODEL_PROVIDER`、`JOB_AGENT_MODEL_NAME`、`JOB_AGENT_MODEL_API_KEY`,可选 `JOB_AGENT_MODEL_BASE_URL`(设置后走 OpenAI 兼容的 chat-completions,禁用 Responses API)。

`JOB_AGENT_API_KEYS` **不在** `Settings` 中 —— 它在 `job_agent/api/security.py` 里直接用 `os.getenv` 读取,必须是 JSON 对象,映射 API key → 用户 ID(如 `{"my-token":"user-001"}`)。缺失时所有认证端点返回 503。认证方式是 `X-API-Key` 请求头;解析出的用户 ID 贯穿整个请求链路。健康检查 `GET /health` 不需要密钥、不依赖模型初始化(模型未配置时会话/历史端点照常工作)。

运行时状态在 `data/`(已 gitignore):`agent_checkpoints.sqlite`、`agent_memory.sqlite`、`agent_sessions.sqlite`。

## 架构

### 两个图入口 —— 不要混淆

- `langgraph.json` 指向 `job_agent/graph.py:graph`,即 `build_graph()` 的编译结果 —— 一个 START → idle → END 的最小图,仅供 LangGraph Agent Server(`langgraph dev`)使用,不是真正的 Agent。
- `create_tool_call_graph()`(同在 `graph.py`)才是真正的 Agent:`agent` ↔ `tools` 循环,由 `tools_condition` 路由。agent 节点绑定 `ToolRegistry` 的工具,store 存在时把长期记忆作为首条 SystemMessage 注入;启用 store 时 `configurable.user_id` 必填。FastAPI 应用、`scripts/chat.py`、集成测试全部走这条路。

API 每个请求都重新编译图(`run_chat`、`stream_chat`、messages 端点、delete),每次都新开 `SqliteSaver` + `SqliteStore` 上下文管理器。改 `app.py` 时遵循该模式。

- messages 端点(历史读取)用 `create_history_read_graph(checkpointer)`(与聊天图**拓扑同构**、节点为空操作的只读图)调 `get_state`,而非手读检查点的 `channel_values`:LangGraph 只在读取图重算出相同未处理任务时才应用**待应用写入**(checkpoint_writes),拓扑不一致会静默漏掉。真实 SQLite 兼容性由 `tests/api/test_history_read.py` 钉死(含 `put_writes` 注入的待应用写入用例);**不要手写内部合并协议**。

### 流式输出(SSE)

- `POST /chat/stream` → `graph.stream(..., stream_mode="messages")`:LangGraph messages 模式经 LLM 回调逐 chunk 转发。实测(当前 langchain-openai 1.x + messages 模式)下,节点内 `invoke()` 同样产生增量 token 事件;模型服务端(Strata/OpenAI 兼容)实测支持真流式。修改图节点或升级 langchain 后,用 `python -m scripts.check_live_stream` 重新验证该行为。
- SSE 契约:命名事件 `token`(`{"content": ...}`)、`done`(`{"conversation_id": ...}`)、`error`(`{"message": ...}`),由 `sse_event()` 生成;`stream_chat` 吞掉异常并发 `error`。前端 `client.js` 手动解析,未见 `done` 会抛错 —— 改后端事件名必须同步改前端。
- 前端流式渲染铁律:`useChat.js` 中追加 token 必须通过 `messages.value[index].content += token`(响应式代理)。**直接修改 push 前持有的原始对象会绕过 Vue 代理,导致只在流结束后整段显示** —— 这正是历史 bug,`useChat.test.js` 用门控流(stream 中途暂停)回归测试锁死该行为。
- UTF-8 中文:解码必须用 `TextDecoder(stream: true)`;SSE 解析器在 `frontend/src/api/sse.js`(`createSSEParser` 纯状态机,跨 chunk 事件/CRLF/注释行/多 data 行均在 vitest 覆盖)。

### 两层持久化

- **Checkpointer**(`SqliteSaver`,`data/agent_checkpoints.sqlite`):按会话的消息状态。线程 ID 由 `build_thread_id()` 生成,是 JSON 数组 `"[\"<user_id>\",\"<conversation_id>\"]"` —— 所有 chat/session 端点都必须用它。
- **Store**(`SqliteStore`,`data/agent_memory.sqlite`):跨会话长期记忆,命名空间 `("users", <user_id>, "memories")`,单一 key `profile`(记忆 key → 值的扁平字典)。

长期记忆**绝不由聊天路径写入**。写入只走 `create_memory_approval_graph()`(`memory/approval_graph.py`):经结构化输出提取候选(`memory/extractor.py`,`validate_memory_candidate` 强制 snake_case key、`source_quote` 必须与用户原文精确匹配)→ LangGraph `interrupt()` 人工审批 → 用同一 `thread_id` 以 `Command(resume=True|False)` 恢复 → 合并进 `profile` 字典。它需要自己的 checkpointer。

### 附件契约(M1 首个增量)

`ChatRequest.attachments`(kind=image/audio/file,数量 ≤3):图片是唯一可读通道,必带 base64(解码 ≤5MiB),由 `build_user_message()` 转成 `image_url` content block,元数据同时存入 `additional_kwargs["attachments"]`(经检查点往返;历史端点回读元数据与文本块,**不回传 base64**)。audio+data 随 M1-c 打开(用户拍板"录音发送转写"):魔数必须音频族、≤20MiB、声明 size 必须等于解码后字节数;字节持久化到 `api/audio_store.py`(`data/agent_audio.sqlite` 的 `audio_blobs` 表,**挂会话生命周期**:`DELETE /sessions/{id}` 同批删该会话 blob),`audio_id` 进元数据/历史/防呆提示。语音内容仍**绝不送 LLM 网关**(网关对 `input_audio` 静默丢弃,实测模型回复 NO_AUDIO)——转写走**按需工具调用**:`asr/engine.py`(faster-whisper **small/int8 CPU**,懒 import + 进程级单例,时长/字符双截断;配置 `JOB_AGENT_ASR_MODEL/ASR_COMPUTE_TYPE/ASR_MODEL_CACHE_DIR/ASR_MAX_SECONDS`,权重放 `data/asr_models`、离线可设 `HF_HUB_OFFLINE=1`;HF 缓存真实布局在 `hub/` 子目录且是符号链接,复制须 `cp -rL`;依赖 pin 三件套 `faster-whisper>=1.2.1,<2` + `ctranslate2>=4.9.0,<5` + `av>=13.1.0,<14`,av 19 与 faster-whisper 1.2 不兼容,不可升级)+ `transcribe_audio` 工具(`job_agent/tools/builtin.py`,`config: RunnableConfig` 注入 user_id——**不在 args schema 里**,取 blob 守用户边界;engine 加载路径无离线单测,靠 warmup/live 探针验证)。`graph.py` 的 `_unreadable_attachment_note` 在每次模型调用的消息**副本**上追加防呆 SystemMessage(不写回 state),三档:`extract_status == "parsed"` 跳过;带 `audio_id` 的语音提示"用对应 audio_id 调用 transcribe_audio,未拿到转写结果前绝不声称听过";其余"无法读取"。纯附件消息允许空文本。**M1-a 识别层**:`job_agent/api/attachment_types.py`(纯函数)提供 `sniff_resolved` 魔数嗅探(权威仲裁,图片 data 非真实图片字节直接 422;HEIC 按 ftyp 品牌归图片族)、`declared_resolved` 声明推断与 `ROUTE_POLICIES` 路由策略表(文档执行器随 M1-b 接线;ASR 执行器与压缩包已随 M1-c 接线——asr 通道注入方式 `tool_call`,archive 执行器 `parse_archive`)+ `PARSEABLE_RESOLVED` 门禁;前端 `utils/attachments.js` 有同源镜像嗅探表(`sniffFileFamily`/`sniffSelectionProblem`)做入口校验,两侧测试各自钉死条目,限额以 `MAX_IMAGE_BYTES` 跨模块断言对齐。**M1-b 文档/文本通道(轻量链,用户拍板不用 torch)**:`attachment_parsers.py` 的 `parse_attachment`(markitdown[pdf,docx,pptx,xlsx] + 内置解码 utf-16 BOM/utf-8-sig/gbk,解不动即 `ParseFailure` 绝不静默替换;空文本/乱码判失败——pdfminer 宽容,垃圾 `%PDF` 出乱码文本不抛异常,口径是 `show_errors=False` + 空判失败;`MAX_EXTRACTED_CHARS=30000` 截断)。file+data 仅在魔数落在 `PARSEABLE_RESOLVED`(pdf/docx/xlsx/pptx/text/zip)时被接收(限额 20MiB/1MiB,声明 size 必须等于解码后字节数),`build_user_message` 请求期就地转译为带 `[文档解析自 <name>,转译文本可能有格式损失]` 标记的 text block(zip 走 `[解包自 <name>,…]` 标记),失败记 `extract_status: "failed"`;zip 由 `parse_archive` **纯内存解包、永不落盘**(用户拍板预设护栏:条目 >200 或解压总量 >200MiB 整体失败;单条压缩比 >200、加密条目、嵌套压缩包、非 document/text 族成员、超同族限额 → 跳过并在标记里点名;成员按魔数重路由回 `parse_attachment`,共享 `MAX_EXTRACTED_CHARS` 预算,预算耗尽即停并标记;成员字节与解包产物均不持久化);元数据经 `additional_kwargs` → 历史 `AttachmentInfo.extract_status`("parsed"/"failed"/null),前端 `attachmentHint` 出徽章,字节不持久化。前端分类/校验/投影全在 `frontend/src/utils/attachments.js`(ChatInput 三入口按钮,useChat 展示形态剥离 data、图片用 data URL 即时缩略图)。微信式"按住说话"在 `ChatInput.vue`(鼠标/触摸长按或空格长按 ≥350ms,松开发送,Esc 取消,>20MB 不发送)经 `utils/voiceRecorder.js` 封装 MediaRecorder/getUserMedia——M1-c 起 webm 字节转 base64 随消息上行(走同一 audio data 通道),客户端不保留语音字节、语音内容仍绝不进模型。前端 audio 限额经 `MAX_AUDIO_BYTES` 与后端对齐。复测能力:`python -m scripts.check_model_multimodal`。

### 会话元数据 vs 对话状态

会话标题/时间戳在 `api/session_store.py` 手写的 sqlite 表(`agent_sessions.sqlite`)。chat、stream、messages、delete 端点都先校验该用户名下存在会话行,否则 404;聊天前必须先 `POST /sessions`。`DELETE /sessions/{id}` 同时删除会话行、checkpoint 线程(`checkpointer.delete_thread`)与该会话的语音 blob(`audio_store.delete_audio_for_session`,幂等)。

### 前端结构

- `App.vue` 只负责布局与组合;业务状态全部在 `composables/useChat.js`(sessions / messages / loading / error / 流式 / AbortController 取消 / 会话 CRUD)。
- 组件:`Sidebar.vue`(会话列表+删除)、`ChatWindow.vue`(消息列表+自动滚动+错误条)、`ChatInput.vue`(Enter 发送、Shift+Enter 换行、生成中显示 Stop 按钮)、`MarkdownContent.vue`(markdown-it + DOMPurify,保留)。
- `api/client.js` 封装 fetch + `api.streamChat(conversationId, message, {onToken, signal})`;解析状态机独立在 `api/sse.js` 以便测试。

## 测试约定

- 后端测试不访问真实 LLM。集成测试用假模型对象 —— 见 `tests/integration/test_tool_flow.py` 的 `FakeToolCallingModel`(实现 `bind_tools`,返回脚本化 `AIMessage`)。
- API 测试调用 `create_app(chat_handler=fake_handler)` —— chat handler 就是为测试注入而设计的 —— 配合 autouse fixture 设置 `JOB_AGENT_API_KEYS`,并用 `patch_graph_dependencies()` 对 `job_agent.api.app` 模块上的 `SqliteSaver.from_conn_string`、`open_memory_store`、`create_chat_model`、`create_tool_registry`、`create_tool_call_graph` 打 monkeypatch。新的触碰图的端点需要同样打桩。
- 前端流式测试:受控 `ReadableStream` + 门控 promise,保证在流中途做断言(见 `frontend/src/composables/useChat.test.js`);SSE 协议细节测试在 `frontend/src/api/sse.test.js`。
