# Music-AI 项目面试梳理

> **项目定位**：AI 驱动的音乐处理平台，支持音频上传 -> 源分离 -> 乐器识别 -> MIDI 转录 -> 鼓点拆分 -> 用户自定义采样库播放的完整 pipeline。

---

## 一、项目概述（面试时说）

Music-AI 是一个端到端的音频 AI 处理平台。用户上传一段音频后，系统会：
1. 用 **Demucs** 分离出人声/鼓/贝斯/其他 4 个音轨
2. 用**乐器分类器**对"其他"音轨进一步拆分（钢琴、吉他、弦乐、合成器）
3. 用 **Basic Pitch** 将每个音轨转录为 polyphonic MIDI
4. 用**鼓点检测器**将鼓组细分为 19 个部分（kick、snare、各种 cymbal 等）
5. 生成音乐分析报告（BPM、调性、和弦、段落）
6. 用户可上传自定义鼓采样库，在浏览器中通过 Web Audio API 重新渲染鼓点

架构上采用**前后端分离**：React + Vite 前端，FastAPI + Celery + PostgreSQL + Redis 后端，支持 Docker Compose 和 systemd 两种部署方式。

---

## 二、整体架构图

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   React 19 SPA  │◄───►│  FastAPI API    │◄───►│  PostgreSQL 14  │
│  (Vite + TS)    │ WS  │                 │     │  (Alembic)      │
└─────────────────┘     │  ┌───────────┐  │     └─────────────────┘
                        │  │  Celery   │  │            ▲
                        │  │  Worker   │◄─┘            │
                        │  └─────┬─────┘               │
                        │        │ Redis pub/sub        │
                        └────────┴──────────────────────┘
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
               ┌────────┐  ┌──────────┐  ┌──────────┐
               │ Demucs │  │Basic Pitch│  │ librosa  │
               │(分离)  │  │(MIDI转录)│  │(鼓检测)  │
               └────────┘  └──────────┘  └──────────┘
```

---

## 三、技术栈详解（面试重点）

### 3.1 后端核心

| 技术 | 版本 | 用途 | 面试重点 |
|------|------|------|----------|
| **Python** | 3.12 | 后端语言 | 必须用 3.12，因为 Demucs / Basic Pitch 的 wheel 只适配 3.12 |
| **FastAPI** | >=0.110 | Web 框架 | 异步/同步混合、依赖注入（Depends）、路由前缀、CORS 中间件 |
| **Pydantic** | >=2.5 | 数据验证 + 配置管理 | `BaseSettings` 加载 `.env`；`Field` 默认值；`field_validator` 自定义解析（如 CORS 字符串->列表） |
| **SQLAlchemy** | 2.0 | ORM | `Mapped` + `mapped_column` 新语法；`with_variant` 兼容 SQLite 测试；关系定义 |
| **Alembic** | >=1.13 | 数据库迁移 | 初始迁移含 `trigger` 自动更新 `updated_at`；enum 类型声明；partial unique index（sample_library 单激活） |
| **psycopg2-binary** | >=2.9 | PostgreSQL 驱动 | `postgresql+psycopg2` 连接串；生产用 `pgbouncer` |
| **Celery** | >=5.4 | 异步任务队列 | `acks_late=True`（worker 崩溃后任务重入队）、`worker_prefetch_multiplier=1`（长任务不批量）、`task_track_started` |
| **Redis** | >=5.0 / 6+ | 消息队列 + 缓存 + pub/sub | Celery broker/result backend；WebSocket 实时进度推送 |
| **passlib[bcrypt]** | >=1.7.4 | 密码哈希 | bcrypt 72 字节输入限制；`CryptContext` 多 scheme 支持 |
| **python-jose** | >=3.3 | JWT 签名/验证 | HS256 对称签名；`type` claim 区分 access/refresh token；`sub` 标准 claim |
| **prometheus-client** | >=0.20 | 监控指标 | `http_requests_total` 计数器、`http_request_duration_seconds` 直方图、`music_ai_tasks_total` 状态 gauge |
| **httpx** | >=0.27 | HTTP 客户端 | 测试用 `TestClient` 底层就是 httpx |

### 3.2 音频 AI 核心

| 技术 | 用途 | 面试重点 |
|------|------|----------|
| **Demucs** (Meta) | 4-stem 源分离（人声/鼓/贝斯/其他） | 模型版本 ht-demucs；CPU 上首跑慢；失败 fallback 到 placeholder stems |
| **Basic Pitch** (Spotify) | 多音轨音频 -> polyphonic MIDI | ONNX 推理；`predict()` 参数（onset_threshold, frame_threshold, min_note_length）；fallback 到 librosa.pyin |
| **librosa** | 音频处理瑞士军刀 | `pyin`（基频检测）、`onset_detect`（鼓点 onset）、`feature.spectral_centroid`（频谱质心）、`feature.rms`（能量）、`load` / `frames_to_time` |
| **soundfile** | 音频读写 | `sf.info()` 探测时长；WAV/FLAC/OGG 支持 |
| **pretty_midi** | MIDI 读取/验证 | 用于 e2e 测试中的 round-trip 验证 |
| **mido** | MIDI 低级操作 | `MidiFile`/`MidiTrack`/`Message`；`bpm2tempo` / `second2tick`；自定义 GM setup messages |

### 3.3 前端核心

| 技术 | 版本 | 用途 | 面试重点 |
|------|------|------|----------|
| **React** | 19 | UI 框架 | hooks（useEffect, useRef）；函数组件；状态管理 |
| **TypeScript** | ~5.6 | 类型安全 | 严格类型定义（AudioTask, StemInfo 等接口） |
| **Vite** | 6 | 构建工具 | 快速 HMR；proxy 配置（开发时 `/api` 代理到 backend） |
| **TanStack Query** | >=5.62 | 服务端状态管理 | `useQuery` / `useMutation`；query key 缓存；WS 直接 patch cache |
| **Tailwind CSS** | 4 | 样式 | utility-first；`@tailwindcss/vite` 插件 |
| **react-router-dom** | 7 | 路由 | SPA 路由；`try_files $uri $uri/ /index.html` 的 nginx 配合 |
| **axios** | >=1.7 | HTTP 请求 | baseURL 配置；拦截器（JWT Bearer token） |
| **wavesurfer.js** | >=7.12 | 波形可视化 | 音频波形渲染；播放控制 |
| **i18next** | >=26.3 | 国际化 | `react-i18next` + `browser-languagedetector`；多语言 JSON 资源 |
| **Web Audio API** | 原生 | 浏览器音频播放 | `AudioContext` + `AudioBuffer` + `GainNode` + `requestAnimationFrame` 播放头 |

### 3.4 DevOps & 部署

| 技术 | 用途 | 面试重点 |
|------|------|----------|
| **Docker Compose** | 多容器编排 | 4 服务：postgres、redis、api、worker；健康检查依赖；命名卷持久化 |
| **nginx** | 反向代理 + 静态文件 | TLS 终止；WebSocket upgrade 代理；`proxy_read_timeout 600s`；SPA fallback |
| **systemd** | 裸机部署服务管理 | `music-ai-api.service` + `music-ai-worker.service`；`ProtectSystem=strict` 安全加固 |
| **certbot** | Let's Encrypt TLS 证书 | 自动续期；`--nginx` 插件 |
| **GitHub Actions** | CI/CD | 并发控制（`concurrency` group）；pytest 后端 + tsc + vite build 前端；Redis service container |
| **Prometheus** | 监控 | `/metrics` 端点；`music_ai_tasks_total{status}` gauge 告警 |

---

## 四、核心功能模块（10 个 Feature）

### Feature 1: 乐器级 MIDI 转录
- Demucs 4-stem 分离 -> 乐器分类器（频谱特征 + 启发式规则）-> Basic Pitch 逐轨转录
- 输出：`detected_instruments` 概率列表 + 各乐器 MIDI 文件

### Feature 2: 19-part 鼓点拆分
- librosa onset 检测 + 频谱质心/峰值频率/包络分类 -> 19 个 GM 打击乐部分
- 输出：每部分独立的 `drums_<part>.mid` + 合并 `drums.mid` + `drums_events.json`

### Feature 3: GM/XG Bank 映射
- 每个 MIDI 文件头部写入标准 GM setup sequence：Bank MSB (CC0) -> Bank LSB (CC32) -> Program Change -> Volume (CC7) -> Expression (CC11) -> Pan (CC10) -> Sustain (CC64)
- 鼓通道固定 channel 9，旋律通道各用不同 channel

### Feature 4: MIDI 控制器（CC）
- Velocity 通过 `velocity_from_strength` sqrt 曲线映射到 35-127
- 每个音符前重置 pitch bend 到 0（防止跨音符滑音泄漏）
- 长和弦自动插入 sustain pedal 事件

### Feature 5: 用户自定义采样库 + 浏览器播放
- 数据库：`sample_libraries` + `sample_files`（GM note 索引）
- 上传：支持多文件或 zip，文件名 alias 映射到 GM 音符（60+ 别名）
- 激活：原子事务切换 `is_active`，partial unique index 保证唯一激活
- 播放：Web Audio API 解码 `AudioBuffer`，按 `drums_events.json` 的时间戳调度 `GainNode`

### Feature 6: 用户账户、认证、权限与配额
- bcrypt 哈希 + HS256 JWT（access 24h + refresh 30d，rotate on use）
- `AUTH_REQUIRED` 可选开关（开发默认关闭，生产强制开启）
- 每用户配额：`max_tasks` + `max_upload_bytes`，429 提前拦截
- Bootstrap admin：首次启动从环境变量自动创建管理员

### Feature 7: WebSocket 实时进度
- `WS /api/ws/tasks/{id}/progress`：初始 snapshot + Redis pub/sub 中继 + DB 兜底轮询
- Token 通过 query param（浏览器 WS 不支持自定义 header）
- 指数退避重连（上限 10s），1008 错误码终止重试
- 前端直接 patch React Query cache，无需额外状态管理

### Feature 8: 健康探针与监控
- `/healthz`：存活探针（只检查进程）
- `/readyz`：就绪探针（检查 Postgres + Redis 连通性）
- `/metrics`：Prometheus 格式，含任务状态 gauge

### Feature 9: CI/CD
- PR / push 触发：pytest 后端、TypeScript 类型检查、Vite 构建
- e2e：Playwright 测试，Postgres + Redis service containers
- 并发控制：同一分支新 push 取消旧 run

### Feature 10: 生产部署
- Compose 路径：staging / demo，一键 `docker compose up -d`
- 裸机路径：systemd + nginx + certbot，含安全加固（`NoNewPrivileges`, `ProtectSystem`）
- 备份：nightly `pg_dump` + `tar` 存储目录

---

## 五、数据处理 Pipeline（核心面试题）

```
用户上传音频
    │
    ▼
┌─────────────┐  POST /api/audio/upload
│ 文件校验     │  -> 检查音频格式/大小
│ 配额检查     │  -> 429 如果超出 max_tasks
└──────┬──────┘
       │
       ▼
┌─────────────┐  task_id 生成，文件写入 storage/uploads/task_<id>/
│ DB 创建任务  │  status = UPLOADED
└──────┬──────┘
       │
       ▼
┌─────────────┐  POST /api/tasks/{id}/process
│ 提交 Celery  │  -> 任务入队，worker 消费
│ 任务队列     │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────────────┐
│                      Worker Pipeline                   │
├───────────────────────────────────────────────────────┤
│ 10%  Preparing audio...                               │
│ 30%  Separating stems...   -> Demucs (4 stems)        │
│ 50%  Splitting instruments... -> 分类器处理 "other"    │
│ 72%  Transcribing to MIDI... -> Basic Pitch 逐轨        │
│ 88%  Mapping GM/XG MIDI... -> MIDI CC setup + 映射      │
│ 94%  Analyzing music... -> BPM/key/chord/sections      │
│ 98%  Writing commentary... -> LLM 生成（可选）          │
│ 100% Done -> status = FINISHED                          │
└─────────────────────────────────────────────────────┘
       │
       ▼
前端通过 WS / 轮询获取进度
       │
       ▼
下载 stems (.wav)、MIDI (.mid)、analysis (.json)
浏览器播放（Web Audio API + 用户采样库）
```

**Pipeline 中每个步骤的 _report() 调用：**
- 更新 DB `progress` + `current_step`
- `db.commit()` 持久化
- `redis.publish(f"task:{id}", payload)` 推送到 WebSocket 客户端

---

## 六、面试必问知识点（按维度）

### 6.1 Python / FastAPI

- [ ] FastAPI 的依赖注入系统（`Depends`）如何工作？`get_db` 为什么是 `yield` 生成器？
- [ ] Pydantic `BaseSettings` 的配置优先级（环境变量 > `.env` > 默认值）
- [ ] `Annotated[..., Depends(...)]` 是 Python 3.9+ 的什么语法？
- [ ] FastAPI 中同步函数（DB 操作）为什么不会阻塞事件循环？（因为 Starlette 会在线程池运行）
- [ ] 路由前缀 `prefix="/api/audio"` 如何与 `include_router` 配合？
- [ ] `HTTPException` 的 `headers` 参数在 401 时为什么要有 `WWW-Authenticate`？

### 6.2 数据库与 ORM

- [ ] SQLAlchemy 2.0 的新语法：`Mapped[int]` vs `Column(Integer)` 的区别
- [ ] `with_variant(Integer, "sqlite")` 的作用（BigInteger -> Integer 兼容 SQLite）
- [ ] Alembic 迁移文件中 `op.execute("""CREATE TRIGGER ...""")` 做什么？（自动更新 `updated_at`）
- [ ] PostgreSQL enum 类型如何声明？`native_enum=True` 的利弊
- [ ] `eager_defaults=True` 的 `__mapper_args__` 是什么意思？（立即获取 server_default 值）
- [ ] 部分唯一索引（partial unique index）在 sample_library 中如何实现"单激活"约束？
- [ ] 数据库连接池在生产中如何优化？（pgbouncer 在 50+ 并发时引入）

### 6.3 异步任务（Celery + Redis）

- [ ] 为什么 `acks_late=True`？（worker 崩溃后任务不丢失，重新入队）
- [ ] `worker_prefetch_multiplier=1` 对 CPU-bound 音频任务的意义？（防止内存膨胀和队头阻塞）
- [ ] Celery 的 `include=["app.tasks_audio"]` 做了什么？（任务发现）
- [ ] Redis 在这里同时是 broker、result backend、pub/sub 三层用途
- [ ] 如果 Redis 不可用，WebSocket 如何降级？（DB 轮询 fallback）

### 6.4 认证与安全

- [ ] bcrypt 的 72 字节输入限制是什么？项目中如何规避影响？（密码限制 128 字符，UTF-8 后通常 < 72 字节）
- [ ] 为什么 access token 和 refresh token 用同一个 key 但 `type` claim 区分？（防御深度：refresh token 不能误当 access token 用）
- [ ] JWT 的 `sub` claim 标准含义是什么？（subject = 用户 ID）
- [ ] `AUTH_REQUIRED` 的开关设计为什么对现有测试很重要？（向后兼容，e2e 不用改）
- [ ] 生产环境为什么拒绝 placeholder JWT secret 启动？（`refresh_settings_check` 安全策略）

### 6.5 WebSocket 实时通信

- [ ] 浏览器 WebSocket 为什么不能可靠发送自定义 header？（WS 握手阶段 header 支持不一致）
- [ ] 为什么 token 通过 query param 传递？（`?token=xxx` 的兼容性）
- [ ] WebSocket 的 `1008` 关闭码含义？（policy violation，用于 forbidden / not found）
- [ ] 指数退避重连的算法？（500ms -> 1s -> 2s -> ... -> 10s cap）
- [ ] 为什么前端直接 patch React Query cache 而不是用 useState？（全局状态一致性，列表页和详情页同步更新）
- [ ] Redis pub/sub 的 `get_message(timeout)` 为什么放在 `asyncio.run_in_executor` 中？（防止阻塞 asyncio 事件循环）

### 6.6 音频 AI 与信号处理

- [ ] Demucs 是什么？输出哪 4 个 stems？（vocals, drums, bass, other）
- [ ] Basic Pitch 的 ONNX 推理流程？`predict()` 的参数含义？
- [ ] librosa `pyin` 是什么算法？（pYIN = probabilistic YIN，基频检测）
- [ ] 乐器分类器使用哪些频谱特征？（spectral centroid, bandwidth, rolloff, flatness, ZCR, HF/low-band ratio）
- [ ] 为什么分类器是启发式规则而不是深度学习模型？（无外部模型依赖，轻量）
- [ ] 鼓点检测的 3 个分类维度？（spectral centroid, peak frequency, envelope）
- [ ] `velocity_from_strength` 为什么用 sqrt 曲线？（人耳对响度的感知是对数/平方根关系）
- [ ] MIDI 的 `ticks_per_beat=480` 是什么？（每拍 480 个 tick，标准时间分辨率）
- [ ] GM setup sequence 的 7 个消息分别是什么？（CC0, CC32, Program, CC7, CC11, CC10, CC64）
- [ ] 为什么每个音符前重置 pitch bend？（防止上一音符的 bend 值泄漏到下一音符）

### 6.7 前端与浏览器音频

- [ ] React Query 的 `queryClient.setQueryData` 如何直接修改缓存？
- [ ] `useTaskProgress` hook 的 `detailKey` 和 `listKey` 为什么用 `as const`？（TS 字面量类型推断）
- [ ] Web Audio API 中 `AudioContext`、`AudioBuffer`、`GainNode` 的关系？
- [ ] 为什么用 `requestAnimationFrame` 驱动播放头而不是 `setInterval`？（与浏览器刷新率同步，更平滑）
- [ ] Vite 的 `import.meta.env.VITE_API_BASE_URL` 是什么？（构建时环境变量注入）
- [ ] Tailwind CSS 4 的变化？（`@tailwindcss/vite` 插件，不再用 `tailwind.config.js`）

### 6.8 DevOps 与部署

- [ ] Docker Compose 中 `depends_on.condition: service_healthy` 的作用？（等 postgres/redis 就绪后再启动 api）
- [ ] nginx 中 `proxy_http_version 1.1` + `Upgrade` + `Connection` header 为什么对 WebSocket 必要？（HTTP/1.1 才支持 upgrade）
- [ ] systemd 的 `ProtectSystem=strict` 和 `ReadWritePaths` 做什么？（安全沙箱，限制文件系统访问）
- [ ] 为什么 worker 的 `--concurrency=2` 而 API 用 `--workers 2`？（worker 是线程并发，API 是多进程）
- [ ] Prometheus 的 counter vs gauge vs histogram 在这个项目中的具体用法？
- [ ] 生产部署的 backup 策略？（pg_dump + tar storage，nightly cron）

### 6.9 测试

- [ ] 为什么测试用 SQLite 而不是 PostgreSQL？（速度、轻量、内存模式）
- [ ] `conftest.py` 中通常定义什么？（fixtures：db session、test client、celery app）
- [ ] 134 个测试覆盖哪些维度？（service 逻辑、API 路由、MIDI 操作、auth、WebSocket、健康检查）
- [ ] `PASSLIB_BCRYPT_FORCE_BACKEND=pure-python` 在 CI 中为什么需要？（GitHub Runner 上原生 bcrypt 扩展不稳定）
- [ ] Playwright e2e 测试什么？（完整用户流程：上传 -> 处理 -> 查看结果）

---

## 七、常见面试问题 & 建议回答要点

### Q1: 为什么用 Celery 而不是直接在后端处理音频？
**答**：音频处理（Demucs、Basic Pitch）是 CPU 密集型且耗时（数十秒到数分钟），如果在 FastAPI 的同步线程池中处理，会阻塞所有请求。Celery 将任务 offload 到独立 worker 进程，API 只负责任务入队和状态查询，保持 HTTP 响应及时。同时 Celery 支持多 worker 横向扩展，适合处理并发上传。

### Q2: 如果 worker 在处理过程中崩溃，任务会怎样？
**答**：由于 `acks_late=True`，worker 崩溃后消息不会确认，会重新入队。下一个 worker 会再次尝试处理。DB 中的 `status` 保持 `PROCESSING`，`claim_for_processing` 逻辑防止双执行。如果任务本身有 bug，最终会进入 `FAILED` 状态，error_message 记录异常。

### Q3: WebSocket 连接断开后如何保证进度不丢失？
**答**：设计了三层保障：1）Redis pub/sub 是实时推送层；2）DB 中的 `progress`/`current_step` 是持久化真相源；3）WebSocket 重连后先发送 `snapshot` 获取当前状态，然后继续监听。即使 Redis 和 WS 都断开，用户刷新页面后仍能从 `/api/audio/{id}` 获取最新进度。

### Q4: 采样库的文件名如何映射到 GM 音符？
**答**：维护了一个 60+ 条目的 alias 表（如 `kick`/`bd`/`bass_drum` -> note 36，`snare`/`sn`/`sd` -> note 38）。上传时解析文件名，匹配 alias 表得到对应的 GM percussion note，存入 `sample_files` 表的 `midi_note` 字段。浏览器播放时按 `drums_events.json` 中的 `note` 字段匹配采样文件。

### Q5: 如何支持多用户但保持开发体验简单？
**答**：`AUTH_REQUIRED` 环境变量开关。开发时设为 `false`，所有端点接受匿名请求，方便本地测试和 e2e。生产时设为 `true`，未认证请求返回 401。同时 `user_id` 列为 nullable，兼容迁移前的旧任务。前端始终发送 `Authorization` header，后端根据开关决定是否强制验证。

---

## 八、快速复习清单（面试前 30 分钟过一遍）

- [ ] 能画出架构图（React -> FastAPI -> Postgres/Redis/Celery）
- [ ] 能说出 pipeline 的 7 个步骤和对应进度百分比
- [ ] 能解释 Demucs -> 分类器 -> Basic Pitch 的数据流
- [ ] 能解释 19-part 鼓点拆分的检测逻辑
- [ ] 能背出 GM setup sequence 的 7 个 MIDI 消息
- [ ] 能解释 JWT access/refresh 的设计和 token rotate
- [ ] 能解释 WebSocket 的 snapshot + pub/sub + DB fallback 三层机制
- [ ] 能解释 Docker Compose 的 4 个服务和 healthcheck 依赖
- [ ] 能解释 pytest 中 SQLite 兼容和 `PASSLIB_BCRYPT_FORCE_BACKEND` 的作用
- [ ] 能解释 Web Audio API 中采样播放的调度机制（AudioContext + GainNode + time offset）

---

> **祝面试顺利！** 重点理解数据流、架构设计决策、以及为什么选这些技术而不是替代方案。面试官通常更关心你如何思考和权衡，而不是纯背诵。
