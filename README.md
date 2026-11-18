# JOB AI ASSISTANT

基于 LangGraph 的 AI 求职助手:Python 后端(`job_agent`,FastAPI 聊天 API + LangGraph Agent Server)+ Vue 3 + Vite 前端(`frontend/`)。

## 项目结构

- `job_agent/`:LangGraph 后端(`api/` FastAPI 与会话、`graph.py` 图、`memory/` 长期记忆与审批流、`tools/` 工具)。
- `frontend/`:Vue 3 + Vite 前端(`npm` 在宿主机运行,不经 Docker)。
- `tests/`:后端测试(unit / integration / api);`scripts/`:冒烟与诊断脚本(访问真实 LLM,需要模型配置)。
- `deploy/`:隔离验收用的 Compose 覆盖层与占位凭据。
- `data/`:dev 服务的手工运行时 SQLite(已 gitignore,勿手工清理)。

## 首次配置

1. `cp .env.example .env`,至少填写:
   - `JOB_AGENT_API_KEYS`:JSON 对象(API key → 用户 ID),必填;未配置时所有认证端点返回 503。
   - `JOB_AGENT_MODEL_*`:模型凭据,仅聊天需要;不配置时健康检查、会话、历史功能照常可用。
2. 前端:`cp frontend/.env.example frontend/.env.local`,把 `VITE_API_KEY` 设为后端 `JOB_AGENT_API_KEYS` 中的一个 key;全新安装(走 Compose `api`)时按示例设 `VITE_API_PROXY_TARGET=http://127.0.0.1:18000`。
3. 环境要求:Docker Compose v2.24+(本项目验证于 v5.7.0,支持 `!override`);Node 24 + npm 11(锁文件 lockfileVersion 3,与 CI 一致)。

## 三类环境,三套命令(切勿混用)

### A. 全新环境(Compose 管理,聊天入口是 `api` 服务)

```bash
docker compose up --build -d
docker compose ps                       # api 应显示 healthy
curl http://127.0.0.1:18000/health      # {"status":"ok"}
docker compose logs -f api
```

- 聊天 API 宿主端口默认 **18000**(`JOB_AGENT_API_HOST_PORT` 可配);LangGraph Agent Server 在 `127.0.0.1:2024`。
- 健康检查不依赖 API Key 与模型配置(`/health`)。
- 前端:`npm run dev` 的 `/api` 代理目标默认 `http://127.0.0.1:8000`;全新环境按 `frontend/.env.example` 设 `VITE_API_PROXY_TARGET=http://127.0.0.1:18000`。

### B. 当前在线环境(兼容入口;不要在其中跑 A/C 的命令)

在线聊天入口仍是 **dev 容器内手工 uvicorn**(宿主 8000,无 `--reload`,改后端代码后须容器内重启),宿主 5173 的 Vite dev server 默认代理到 8000 —— 这是保持兼容的默认值,**不要改真实 `.env` / `.env.local`**。

```bash
docker compose ps                       # 只查看,不 up/restart/down
curl http://127.0.0.1:8000/health       # 在线 uvicorn
curl "http://[::1]:5173/api/health"     # 在线前端代理链路
```

- **警示:在在线环境执行 `docker compose up --build -d` 会重建并 recreate 在线 `dev` 容器 —— 禁止。**
- Vite dev server 只监听 IPv6 回环:`127.0.0.1:5173` 会被拒连,须用 `http://[::1]:5173`。

### C. 隔离验收环境(占位凭据、独立项目名/端口/卷)

所有命令必须带 `--env-file deploy/accept.env`:它把 Compose 的**插值来源**也切到占位文件,项目根目录真实 `.env` 不再被读取(包括未启动服务里的真实 env 文件引用);`-p m0r3-accept` 独立项目名;命令显式指定 `api` 服务:

```bash
docker compose --env-file deploy/accept.env -p m0r3-accept \
  -f compose.yaml -f deploy/compose.accept.yaml up -d --build api
curl http://127.0.0.1:18000/health
docker compose --env-file deploy/accept.env -p m0r3-accept \
  -f compose.yaml -f deploy/compose.accept.yaml restart api
curl http://127.0.0.1:18000/health
# 清理本轮验收资源(容器、网络、卷):
docker compose --env-file deploy/accept.env -p m0r3-accept \
  -f compose.yaml -f deploy/compose.accept.yaml down -v --remove-orphans
docker rmi job_ai_assistant:m0r3-accept
```

- 覆盖层把 `dev` 挂到专属 profile `disabled` 并清空其宿主端口/卷/env_file —— 即便被外部 `COMPOSE_PROFILES` 意外启用也无副作用。
- 验收前端代理链路(可选):在临时前端 env 里设 `VITE_API_PROXY_TARGET=http://127.0.0.1:18000`,`npm run dev -- --port 5199` 后 `curl "http://[::1]:5199/api/health"`。

## api 服务停止与启动

```bash
docker compose stop api
docker compose start api
docker compose restart api
docker compose down        # 停止整个项目(命名卷数据保留;仅全新环境)
```

## 数据持久化

`api` 服务把运行数据放在命名卷 **agent-data**(`/app/data`):重启 API 后测试会话与历史保留(`restart api` 后再查 `/sessions` 验证;隔离验收用独立卷 `agent-data-m0r3`)。

## 迁移切换方案(独立发布任务,尚未执行)

目标:把在线聊天入口(dev 容器手工 uvicorn + 宿主 8000,数据 bind 挂载 `./data`)切到 Compose 管理的 `api` 服务(命名卷 `agent-data`)。**这是单独安排的发布任务,本次仅定稿方案;下方备份/恢复流程已用临时 SQLite 假数据完整演练,未触碰真实业务库。**

1. **停写范围与迁移前检查**:停写对象仅是聊天 API 的写入(dev 容器内手工 uvicorn 进程),LangGraph Agent Server(2024)不在本次范围。窗口内确认无进行中的流式会话;记录三个业务库(`agent_checkpoints/agent_memory/agent_sessions.sqlite`)的文件清单、大小与 mtime,记录在线镜像与容器端口映射,确认目标 compose 配置已通过隔离验收。
2. **一致性备份与原始数据保留**:停掉 uvicorn 后,用 SQLite 在线备份 API(`sqlite3` 的 `BACKUP TO` / `Connection.backup`,无需文件锁假设)把三个库快照到备份目录;对快照跑 `PRAGMA integrity_check`。**`./data` 原件保留、只读不改名不删除**,备份目录与校验输出写入发布记录。
3. **已创建、停止状态的目标容器/卷**:先 `docker compose create api`(不 `up`),确认 `agent-data` 卷与目标镜像(验收产物 digest)就绪;再把备份目录拷入卷:`docker compose cp <备份目录>/. api:/app/data/`。全程 API 不启动。
4. **离线恢复、完整性检查、业务数据核对**:对卷内三个库跑 `PRAGMA integrity_check` 与 `PRAGMA foreign_key_check`;业务核对(只读):`sessions` 行数与停写前清单一致、抽查已知 `conversation_id` 的历史可读(`GET /sessions/{id}/messages`)、记忆 `profile` 键数与停写前记录一致。任一不过 → 不进入第 5 步,按回滚处理。
5. **启动目标、切换入口、回滚条件**:`.env` 设 `JOB_AGENT_API_HOST_PORT=8000`,从 `dev` 服务移除 `"8000:8000"` 映射(改 compose.yaml,走 PR);`docker compose up -d api`。验收:`/health` 200、`/sessions` 列表与停写前一致、历史读取一致、SSE 流式冒烟通过。**回滚条件**:任一项不达标或 API 反复不健康 → `docker compose stop api`,恢复 dev 的 8000 映射与手工 uvicorn(原件 `./data` 从未改动,立即恢复服务),目标卷数据保留用于排查。

## 附件输入(M1 首个增量)

`/chat` 与 `/chat/stream` 的请求体支持 `attachments`,`MessageResponse`(历史)附带其元数据。上限前后端一致:**数量 ≤3;图片解码后 ≤5MiB;可解析文档与压缩包 ≤20MiB;纯文本 ≤1MiB;语音(含录音)≤20MiB**。

- `kind=image` —— 当前模型**唯一可读**的多模态通道(经 `python -m scripts.check_model_multimodal` 实测可复现);必带 base64 `data`,进检查点的是 `image_url` content block;历史接口只回文本与元数据,**不回传 base64**。
- `kind=audio` —— 元数据总是登记;`data` 通道随 M1-c 打开(用户拍板"录音发送转写"):携带的 base64 经魔数仲裁必须落在音频族(webm/wav/mp3/m4a/ogg),解码后 ≤20MiB、声明 size 必须等于解码字节数,否则 422。字节持久化到 `data/agent_audio.sqlite`(`api/audio_store.py`,**挂会话生命周期**:`DELETE /sessions/{id}` 同批删除该会话 blob),元数据带 `audio_id` 并进历史回读。**内容仍绝不送模型/LLM 网关**(网关对 `input_audio` 静默丢弃,实测模型回复 NO_AUDIO)——模型经 `transcribe_audio` 工具按需获取服务端本机转写(见 M1-c 条)。
- `kind=file` —— 元数据总是登记;`data` 通道随 M1-b 打开,**仅限可解析族**:携带的 base64 经魔数仲裁必须落在 `pdf/docx/xlsx/pptx/text/zip`(`PARSEABLE_RESOLVED`,zip 随 M1-c 加入),否则 422(webm 冒充文件即被这样拒掉)。命中门禁的在**请求期就地转译**成文本(见下条),**原始字节不持久化**;未带 `data` 或不可解析的文件仍是"仅元数据"旧契约。
- 空文本 + 附件的纯附件消息合法。
- **M1-a 类型识别层**(`job_agent/api/attachment_types.py`):附件按"声明 mime < 扩展名 < **内容魔数(权威)**"三信号解析;图片 `data` 必须通过魔数嗅探——伪装成 PNG 的 webm/文本/二进制一律 422;HEIC/HEIF 按 ftyp 品牌细分仍走图片通道。路由策略表(通道→执行器→限额→注入方式)已在该模块就位,文档/压缩包执行器已随 M1-b/M1-c 接线,ASR 执行器已随 M1-c 以工具调用通道接线。前端 `frontend/src/utils/attachments.js` 维护同源镜像嗅探表做入口体验校验,后端始终重验。
- **M1-b 文档/文本通道(轻量链)**:`job_agent/api/attachment_parsers.py` 用 `markitdown[pdf,docx,pptx,xlsx]`(纯 Python,无 torch;Docling 推迟为后续独立增量)转译 pdf/docx/xlsx/pptx,纯文本走内置解码(utf-16 BOM/utf-8-sig/gbk,解不动就判失败、绝不静默替换),转译文本 ≤30000 字符(超出截断并标注)作为 `"[文档解析自 <名称>,转译文本可能有格式损失]"` 文本块注入消息;失败只记 `extract_status: "failed"`,附件仍按旧契约呈现"无法解析"。历史 `AttachmentInfo` 带 `extract_status: "parsed"|"failed"|null`,前端据此显示"已解析/解析失败/无法解析"徽章;防呆 SystemMessage 只覆盖未转译成功的附件。
- **M1-c 语音转写 + 压缩包解包(2026-11-18)**:ASR 走**服务端本机推理**——`faster-whisper`(档位拍板 **small/int8 CPU**,中文实测 ≈3.5× 实时;`av` 原生解 webm,无需 ffmpeg;依赖 pin 见 `requirements.txt`,av 19 与 faster-whisper 1.2 不兼容)。`job_agent/asr/engine.py`:懒 import + 进程级单例(首次调用才加载权重),时长/字符双截断(超 `JOB_AGENT_ASR_MAX_SECONDS` 默认 600s 只转前段、输出 >30000 字符截断,均带标记);配置项 `JOB_AGENT_ASR_MODEL` / `JOB_AGENT_ASR_COMPUTE_TYPE` / `JOB_AGENT_ASR_MODEL_CACHE_DIR`(默认 `data/asr_models`,可设 `HF_HUB_OFFLINE=1` 完全离线)。转写是**按需工具调用**(注入方式 B):`transcribe_audio` 注册进 `ToolRegistry`,user_id 经 `RunnableConfig` 注入、取 blob 守用户边界,返回头带语言/时长/截断标记;`audio_id` 缺失/blob 不存在/转写失败都收敛为可读错误文本。压缩包:`parse_archive` 对 zip **纯内存解包、永不落盘**(天然无 zip-slip 面),护栏:条目 >200 或解压总量 >200MiB 整体失败;单条压缩比 >200、加密条目、嵌套压缩包、非 document/text 族成员、超同族限额 → 跳过并在标记里点名;成员按魔数重路由既有解析链,共享 30000 字符总预算(预算耗尽即停并标记);成员字节与解包产物均不持久化。
- 前端"按住说话"(🎙 按钮鼠标/触摸长按,或长按空格 ≥350ms;短按空格恢复为普通空格输入):浏览器 MediaRecorder 现场录音,松开立即发送,Esc 取消,过短(<600ms)只提示不发送,过大(>20MB)只提示不发送。录音 webm 转 base64 **随消息上行**(拍板"录音发送转写"):客户端不保留语音字节、语音内容绝不进模型/网关,仅服务端持久化后由转写工具消费(`frontend/src/utils/voiceRecorder.js`)。
- 防呆:图节点在每次模型调用的**副本**里注入 SystemMessage(不写回检查点/历史),扫描全部历史人类消息,按附件分三档:`extract_status == "parsed"` 跳过;带 `audio_id` 的语音提示"用对应 audio_id 调用 `transcribe_audio` 工具获取转写文本,未拿到结果前绝不声称听过";其余"无法读取,勿假装读过"。图片若存在仍正常可见。
- 换模型或换网关后,用 `python -m scripts.check_model_multimodal` 复测图片/语音能力再调整契约。

## 测试

后端(仓库根目录直接执行,无需任何包装参数;全部存储自动重定向到临时目录,不触碰 `data/`):

```bash
docker compose exec -T dev python -m pytest                 # 全量 + 覆盖率报告
docker compose exec -T dev python -m pytest --cov-fail-under=90   # 与 CI 相同的覆盖率门禁
docker compose exec -T dev python -m pytest tests/unit/test_config.py   # 子集直跑
docker compose exec -T dev ruff check .
docker compose exec -T dev ruff format .
```

前端(`frontend/`):`npm ci`、`npm test`(Vitest,默认测试池)、`npm run build`。Windows PowerShell 下用 `npm.cmd`。

## Docker 构建范围

`.dockerignore` **递归**排除真实环境文件(`.env` 与 `.env.*`,任意层级;仅显式放行 `.env.example`)、业务数据(`data/`、`*.sqlite`)、`deploy/accept.local.env`、Git/编辑器元数据、Python/工具缓存、`node_modules` 与前端构建产物。镜像本身包含 `job_agent/`、`tests/`、`scripts/`、配置示例,可直接 `docker run --rm job_ai_assistant:dev python -c "import job_agent"` 自检(不挂载工作区)。

## CI

`.github/workflows/test.yml`:
- 后端 job:镜像构建 → `ruff check` / `ruff format --check` → `pytest --cov-fail-under=90`,默认**离线**执行,不注入任何模型、付费服务或追踪凭据;
- 前端 job:Node 24 + `npm ci` / `npm test` / `npm run build`;
- LangSmith 追踪:仅当仓库配置了 `LANGSMITH_API_KEY` 时额外运行 opt-in job,与默认离线测试分开。

## 行为契约摘要

- SSE 事件:`token` / `done` / `error`(改后端事件名必须同步 `frontend/src/api`)。
- 历史读取经与聊天图拓扑同构的只读图 `get_state`,可见待应用写入(checkpoint_writes);线程 ID 为 JSON 数组 `["<user_id>","<conversation_id>"]`。
- 长期记忆绝不由聊天路径写入,仅走 `memory/approval_graph.py` 的人工审批流。
