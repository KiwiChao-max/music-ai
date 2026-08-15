# music-ai 项目面试知识点（问答版）

> 基于当前代码库（279 tests, 14 Alembic migrations, 6-stem Demucs, 19-part drum split,
> GM/XG mapping, custom sample library + SF2/CSV import, PostgreSQL + Redis + Celery,
> Docker Compose 一键部署, CI/CD + E2E + Security Scan）。面试前过一遍，
> 重点理解"为什么这样设计"而不是背诵答案。

---

## 一、项目整体

### Q1: 这个项目是做什么的？核心功能有哪些？

这是一个 **AI 音乐处理 Web 应用**，用户上传音频后，系统自动完成端到端处理：

1. **6 轨音源分离** - 用 Demucs `htdemucs_6s` 模型分离 vocals / drums / bass / piano / guitar / other
2. **乐器识别与 MIDI 转录** - 对 other 轨二次分类（piano / guitar / strings / synth / other_melodic），用 Basic Pitch 转成 polyphonic MIDI
3. **鼓组 19 件精细拆分** - kick / snare / hi-hat / 5 个 toms / 5 种 cymbals / 小打 / fill 加花
4. **GM / XG 音色映射** - 生成 GM 和 XG 两个标准变体，带完整 Bank Select + Program Change + CC 控制器
5. **自定义采样库** - 用户上传鼓采样，文件名映射或频谱自动识别 -> GM 音符，浏览器 Web Audio 播放
6. **SoundFont / CSV 音色表导入** - 支持 SF2 文件和电子琴 CSV 音色表，自动映射 GM -> 自定义预设
7. **音乐分析** - BPM、调式、和弦、段落检测 + LLM AI 点评
8. **用户系统** - JWT 认证、配额管理、数据隔离
9. **实时进度** - WebSocket 推送处理进度（Redis pub/sub + DB 轮询降级）
10. **定时任务** - Celery Beat 定时清理过期 Token 和旧任务产物
11. **DevOps** - Docker Compose 一键部署（PostgreSQL 16 + Redis 7 + API + Worker）、GitHub Actions CI/CD（4 个工作流）、Playwright E2E、Security Scan（每周 + push 触发）、pre-commit hooks（Ruff / ESLint / Prettier）

技术栈：**FastAPI + SQLAlchemy 2.0 + PostgreSQL 16 + Redis 7 + Celery + React 19 / Vite + Tailwind v4 + Web Audio API**

---

## 二、音乐知识篇

### Q2: 什么是 GM（General MIDI）？为什么要兼容它？

**GM 是通用 MIDI 标准**，规定了 128 种乐器的编号映射和 47 个打击乐音符映射。

- **旋律声部**：Program Change 0-127 对应 128 种乐器（0=大钢琴，24=尼龙吉他，40=小提琴...）
- **打击声部**：固定使用 **第 10 通道（Channel 9，从 0 开始数）**，音符 35-81 对应不同鼓件
- **意义**：保证同一个 MIDI 文件在任何支持 GM 的音源/合成器/DAW 上听起来音色正确

> 项目中所有生成的 MIDI 都带有完整的 GM setup sequence（Bank MSB/LSB -> Program Change -> CC7 音量 -> CC11 表情 -> CC10 声像 -> CC64 延音 -> CC74 亮度 -> CC91 混响 -> CC93 合唱），确保在任意 DAW 中音色正确。

### Q3: 什么是 XG？和 GM 是什么关系？

**XG 是 Yamaha 提出的 GM 扩展标准**，在 GM 基础上增加了更多音色和效果。

- 通过 **Bank Select（CC0 + CC32）** 切换不同音色库
- GM 旋律用 Bank 0:0；XG 旋律变奏用 Bank 0:1（如 "Live! Grand Piano"）
- XG 鼓组用 Bank MSB=127, LSB=0（Standard Kit）
- GM1 鼓组不使用 Bank Select（channel 10 隐式为鼓组）

> 项目中 `midi_mapping_service.py` 会同时生成 `_gm.mid` 和 `_xg.mid` 两个变体。XG 文件头部写入 XG System On SysEx（`0x43 0x10 0x4C 0x00 0x00 0x7E 0x00`），GM 文件写入 GM System On SysEx（`0x7E 0x7F 0x09 0x01`）。

### Q4: MIDI CC 控制器是什么？项目中用到了哪些 CC？

**CC（Control Change）** 是 MIDI 协议中用于控制音色参数的消息，范围 0-127。

项目中用到的 CC：

| CC 号 | 名称           | 作用                   | 示例值         |
| ----- | -------------- | ---------------------- | -------------- |
| CC 0  | Bank MSB       | 选择音色库（高位）     | GM=0, XG鼓=127 |
| CC 32 | Bank LSB       | 选择音色库（低位）     | XG旋律变奏=1   |
| CC 1  | Modulation     | 调制轮                 | 0              |
| CC 7  | Channel Volume | 通道音量               | 100-112        |
| CC 10 | Pan            | 声像（左右声道）       | 64（居中）     |
| CC 11 | Expression     | 表情控制器（精细音量） | 127            |
| CC 64 | Sustain Pedal  | 延音踏板               | 0（关闭）      |
| CC 74 | Brightness     | 亮度/滤波器截止        | 64-80          |
| CC 91 | Reverb Send    | 混响发送量             | 15-55          |
| CC 93 | Chorus Send    | 合唱发送量             | 0-35           |

> 这些 CC 在 `midi_cc.py` 的 `gm_setup_messages()` 函数中统一构建，鼓组和旋律轨各有不同配置。鼓组不写 brightness/reverb/chorus（打击乐不需要），旋律轨按 stem 类型有不同默认值（如 strings 的 reverb=55, chorus=35 比 piano 的 reverb=40, chorus=0 更湿润）。

### Q5: 什么是 Program Change？和 Bank Select 什么关系？

- **Program Change**：在当前 Bank 内选择具体乐器（0-127）
- **Bank Select**：先选库，再选具体音色
- **顺序**：先发 Bank MSB (CC0) -> Bank LSB (CC32) -> 再发 Program Change

> 为什么要分两步？因为 128 个音色不够用，用 Bank 可以扩展到 128x128=16384 个音色。XG 标准用 LSB 选择同一乐器的不同变奏（如 Bank 0:0 = 标准 Grand Piano, Bank 0:1 = "Live! Grand Piano"）。

### Q6: 鼓组 MIDI 为什么用第 10 通道？

这是 GM 标准的规定：**通道 9（第 10 通道，从 0 开始）是打击乐专用通道**。

- 在这个通道上，Note On 的音符号不代表音高，而代表**不同的鼓件**
- 比如：36=底鼓(Bass Drum 1)、38=军鼓(Acoustic Snare)、42=闭合踩镲(Closed Hi-Hat)、49=坠镲(Crash Cymbal 1)

> 项目中鼓 MIDI 全部写在通道 9 上，每个部件对应一个 GM 标准音符。`drum_midi_service.py` 中的 `_GM_DRUM_NOTES` 字典定义了 19 个部件到 GM 音符的映射。

### Q7: 项目中的 19 个鼓部件分别是什么？

19 个 GM 打击乐部件：

| 类别 | 部件                                               | GM Note            |
| ---- | -------------------------------------------------- | ------------------ |
| 底鼓 | kick                                               | 36                 |
| 军鼓 | snare, sidestick                                   | 38, 37             |
| 踩镲 | hihat_closed, hihat_open                           | 42, 46             |
| 通鼓 | tom_high, tom_himid, tom_lomid, tom_low, tom_floor | 50, 48, 47, 45, 41 |
| 镲片 | crash, ride, china, splash, ride_bell              | 49, 51, 52, 55, 53 |
| 小打 | tambourine, cowbell, percussion                    | 54, 56, 60         |
| 加花 | fill                                               | 47                 |

> 其中 `fill`（加花）是后处理推导的：`_derive_fills` 方法把时间上挨得很近的密集击打点（3+ hits within 450ms，或相邻间隔 < 220ms）归为 fill。原始击打保留在其主部件中，同时镜像一份到 fill 轨，鼓手可以独立编辑加花。

### Q8: 什么是音源分离（Source Separation）？项目用了什么模型？

**音源分离**就是从一首混音歌曲中分离出各个乐器的音轨。

项目用 **Demucs `htdemucs_6s` 模型**，分离 **6 个音轨**：

- **vocals**（人声）
- **drums**（鼓）
- **bass**（贝斯）
- **piano**（钢琴）- 6-stem 模型原生输出
- **guitar**（吉他）- 6-stem 模型原生输出
- **other**（其他所有，包括弦乐、合成器等）

> 相比 4-stem 模型，6-stem 的优势是 piano 和 guitar 作为原生 stem 输出，分离质量远高于从 other 轨二次提取。`instrument_classifier_service.py` 仍对 other 轨做二次分类，提取 strings / synth / other_melodic。

### Q9: 乐器分类是怎么做的？用了什么特征？

用**基于规则的频谱特征分类**，不依赖外部模型，轻量快速。

核心特征（per-frame 计算）：

- **频谱质心（Spectral Centroid）** - 音色"亮"不亮
- **频谱带宽（Bandwidth）** - 频率分布宽度
- **频谱滚降（Rolloff）** - 能量集中在低频还是高频
- **频谱平坦度（Flatness）** - 像噪音还是像乐音
- **过零率（ZCR）** - 信号穿过零点的频率
- **低/中/高频能量比**
- **峰值（Peakiness）** - 频谱峰值锐度，区分谐波乐器和噪音

分类逻辑（`_frame_posterior`）：

- 钢琴：peakiness > 12, centroid 200-3500Hz, flatness < 0.30
- 吉他：centroid 250-3000Hz, flatness 0.10-0.45, HF < 0.25
- 弦乐：rolloff > 2500Hz, flatness < 0.20, HF < 0.10
- 合成器：flatness > 0.30, bandwidth > 1800Hz
- 其他：当以上概率总和 < 0.5 时的 fallback

> 用 **soft mask reconstruction** 重建每件乐器的音频：每帧信号按概率权重分配到各乐器轨，保留重叠音符的能量。优点是快、无依赖、可解释；缺点是精度不如深度学习模型。

### Q10: 采样分类（Sample Classifier）是怎么识别鼓采样的？

和乐器分类思路类似，但针对**短促 one-shot 采样**优化（`sample_classifier_service.py`）：

1. **提取频谱特征**：质心、峰值频率、滚降、能量比、过零率、谐波性、attack ratio
2. **多候选规则引擎**：每种特征组合产生一个候选（drum_type + confidence），最终取置信度最高的
3. **映射到 GM 音符**：通过 `_DRUM_TYPE_TO_GM_NOTE` 字典分配标准 GM 打击乐音符

典型规则：

- 低频能量 > 25% + 峰值频率 < 300Hz -> kick
- 中频为主 + 高频有噪声 + attack > 10 -> snare
- 高频 > 40% + 质心 > 4000Hz + 时长 < 0.08s -> hihat_closed
- 高频 > 25% + 滚降 > 3000Hz + 时长 > 0.3s -> crash

> 用户上传任意命名的采样文件，系统先尝试文件名映射（60+ 别名），匹配失败后通过音频内容自动识别类型并分配正确的 MIDI 音符。这解决了"采样文件名不规范"的真实痛点。

### Q11: 什么是 Basic Pitch？它和传统音高检测有什么区别？

**Basic Pitch** 是 Spotify 开源的**多音高检测（polyphonic pitch detection）模型**，可以从音频中转出复音 MIDI。

区别于传统方法（如 autocorrelation、FFT 峰值检测）：

- 传统方法大多只能检测**单音**（monophonic）
- Basic Pitch 用轻量神经网络（ONNX 推理），可以检测**和弦**等复音
- 输出带力度（velocity）信息

> 项目中 `basic_pitch_service.py` 调用 Basic Pitch 把各乐器音轨转成 MIDI。**两级 fallback 容错机制**：

```
transcribe(audio)
  ↓
1. 优先用 Basic Pitch（_transcribe_with_basic_pitch）
  ↓ 如果 ImportError / ModuleNotFoundError
  （TensorFlow 未安装，如精简 Docker 镜像或不希望引入 ~600MB TF 依赖）
2. fallback 到 librosa.pyin 单音检测（_transcribe_with_librosa）
  ↓ 如果 Basic Pitch 运行时异常（模型崩溃等）
3. 同样 fallback 到 librosa.pyin
```

> 两种路径都会注入完整的 GM setup（Bank Select + Program Change + CC 控制器），区别是 Basic Pitch 路径通过 `_inject_gm_setup` 后处理注入，librosa 路径在 `_write_midi` 中直接写入。librosa.pyin 虽然只能做单音检测，但对 bass、vocal 和简单旋律轨已经足够，生产级复音质量仍依赖 Basic Pitch。

> 补充：TensorFlow 2.16+ 已全面支持 Python 3.12（Linux / macOS / Windows），本地开发环境 Basic Pitch 可直接运行。fallback 路径保留主要是为了不需要 TF 的轻量部署场景（精简 Docker 镜像）。

### Q12: 力度（Velocity）是怎么计算的？

力度范围 0-127，项目用 `velocity_from_strength()` 函数计算：

- 输入：归一化信号强度（0-1）
- 用 **平方根曲线** 映射：`velocity = 40 + 87 * sqrt(normalized)`
- 裁剪到 **35-127** 范围（避免太弱的音符）

> 为什么用平方根？因为人耳对声音响度的感知是**对数级**的，线性映射会觉得"中间区域变化不明显"，用曲线可以让力度变化更自然。

**鼓组 velocity 的特殊处理**：使用 95th 百分位作为参考值（而非最大值），这样最弱的 ghost note 保持安静，最强的 accent 保持响亮，只有 top 5% 的击打被 clamp 到 127。这比逐轨归一化（把所有力度拉伸到 35-127）更能保留真实的演奏动态。

### Q13: 什么是 SoundFont（SF2）？项目怎么用的？

**SoundFont** 是一种采样乐器格式（.sf2 文件），包含：

- 多个 **Preset（预设）** - 每个预设对应一种音色
- 每个预设由多个 **Sample（采样）** 组成，按音高和力度分层
- 包含包络（ADSR）、滤波、颤音等合成参数

项目中 `soundfont_service.py` 支持：

1. **SF2 导入**：优先用 `sf2utils` 库完整解析（pbag/pgen/pmod 链），fallback 到简化 phdr chunk 解析（mmap 避免大文件内存爆炸）
2. **CSV 音色表导入**：支持电子琴音色表（bank_msb, bank_lsb, program, name, category, instrument_type）
3. **GM -> 自定义映射**：三级匹配策略（instrument_type 精确匹配 -> program 精确匹配 -> 名称 token 模糊匹配）

> 用户上传 SF2 或 CSV 音色表后，系统自动为每个 stem 找到最接近的自定义预设，在 MIDI 映射时替换默认 GM 音色。映射结果记录在 `analysis.json` 的 `soundfont_overrides` 字段中，前端显示"Stem X -> Custom voice Y"。

### Q14: BPM 和调式（Key）是怎么检测的？

- **BPM 检测**：通过 onset detection（起始点检测）分析节拍间隔，用 librosa.beat.beat_track
- **调式检测**：分析音高分布，对照大/小调的音程模式计算匹配度
- **和弦检测**：基于音高类分布（pitch class profile）匹配和弦模板

> 鼓组 BPM 检测有自适应 onset 参数：慢速（<80 BPM）用高阈值避免 reverb tail 误触发；快速（>170 BPM）用低阈值避免漏检 blast beat。

### Q15: Web Audio API 播放采样的原理是什么？

浏览器端播放鼓采样的流程：

1. **加载**：`fetch()` 获取音频文件 -> `AudioContext.decodeAudioData()` 解码成 `AudioBuffer`
2. **缓存**：解码后的 AudioBuffer 存在内存里，避免重复解码
3. **调度**：`AudioBufferSourceNode` 调度播放 -> `GainNode` 控制力度 -> 连接到 `destination`
4. **时间精度**：用 `AudioContext.currentTime + offset` 精确调度（比 setTimeout 准得多）
5. **力度控制**：通过 GainNode 的 gain 值实现 velocity 效果
6. **Velocity 层选择**：前端根据 MIDI velocity 在同一 note 的多个采样层中选择最接近的（如 v1-50 选 soft 层，v51-100 选 hard 层）

> 项目中 `SampleBasedDrumPlayer.tsx` 消费后端生成的 `drums_events.json`（时间戳 + note + velocity + part），用 Web Audio API 按时间调度采样播放。

---

## 三、系统架构篇

### Q16: 项目的技术架构是什么？

**前后端分离 + 异步任务处理**架构：

```
前端 (React 19 / Vite / TypeScript)
    ↓ HTTP / WebSocket
API 层 (FastAPI)
    ↓
PostgreSQL 16 (主数据库) + Redis 7 (消息队列/缓存/PubSub)
    ↓ Celery (异步 Worker) + Celery Beat (定时任务)
Worker 进程 (Demucs + Basic Pitch + Drum MIDI + MIDI Mapping + Analysis)
```

- **同步**：用户上传、查询等请求直接由 FastAPI 处理
- **异步**：音频处理耗时，通过 Celery 后台执行，WebSocket 推送进度
- **定时**：Celery Beat 每天清理过期 refresh token 和旧任务文件
- **部署**：Docker Compose 一键启动全部服务（PostgreSQL + Redis + API + Worker），非 root 用户运行
- **数据库**：从 SQLite 开发阶段迁移到 PostgreSQL 16，通过 Alembic 管理 14 个迁移版本

### Q17: 为什么用 Celery？可以不用吗？

因为音频处理（Demucs 分离、Basic Pitch 转 MIDI）是**计算密集型任务**，可能需要几十秒到几分钟：

- 同步处理会导致 HTTP 请求超时
- Celery + Redis broker 可以后台异步执行，前端通过轮询或 WebSocket 看进度
- 支持任务队列、重试、并发控制

> 项目中 `audio_worker.py` 就是 Celery worker，处理流程是一个 pipeline：上传 -> 分离 -> 乐器分类 -> 转 MIDI -> GM/XG 映射 -> 分析 -> AI 点评 -> 完成。每一步调用 `_report()` 更新进度并推送到 WebSocket。

> 除此之外，`tasks_scheduled.py` 通过 Celery Beat 运行定时任务（每天清理过期 refresh token、清理旧任务产物），保持数据库和磁盘整洁。

> 任务可靠性方面：`worker_claim` 使用 CAS（Compare-And-Swap）原子操作防止重复处理，并内置 35 分钟超时恢复机制——如果 Worker 崩溃/OOM，超时后任务会被自动回收重新处理。macOS 上需设置 `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` 避免 prefork 子进程因 ObjC 运行时 fork 安全问题 SIGABRT 崩溃。

### Q18: 采样库的"单激活"（一个用户只能有一个活跃库）是怎么实现的？

用 **部分唯一索引（Partial Unique Index）** 保证数据一致性：

- 数据库层：在 `sample_libraries` 表上建一个 `WHERE is_active = 1` 的唯一索引
- 应用层：激活新库时，在一个事务里 `UPDATE sample_libraries SET is_active = 0`，再设新库 `is_active = 1`
- 为什么数据库层也要保证？防止应用层有 bug 或并发操作导致多个激活库

### Q19: WebSocket 实时进度是怎么实现的？

经典的 **发布-订阅（Pub/Sub）模式**：

1. Worker 每完成一个步骤，就往 Redis 的 `task:{id}` channel 发一条进度消息
2. WebSocket 客户端连接后，先从 DB 拿当前状态（snapshot），然后订阅 channel
3. 收到新消息就推给前端，前端直接 patch React Query 缓存
4. 任务结束（FINISHED/FAILED）后关闭连接

**降级策略**：Redis 不可用时，自动退化为 5 秒轮询 DB。

**安全措施**：WebSocket 连接有 per-IP 连接数限制和生命周期限制，防止资源耗尽。Token 通过 query param 传递（浏览器 WS 不支持自定义 header）。

### Q20: JWT 认证是怎么做的？有什么安全措施？

**HS256 算法**，双 token 机制：

- **Access Token**：15 分钟有效期（在内存中，不落 localStorage），用于 API 调用
- **Refresh Token**：7 天有效期，持久化到 `refresh_tokens` 表，用于换新的 access token
- **Refresh Token 轮换**：每次用 refresh token 换新 token 时，旧 token 失效并签发新 token（防止重放攻击）
- **自动清理**：Celery Beat 每天定时清理过期 token（`purge_expired_tokens`）
- **类型区分**：access token 不能当 refresh token 用，反之亦然（payload 里有 type 字段）

密码用 **bcrypt** 哈希（passlib 库）。

### Q21: 用户配额（Quota）是怎么实现的？

两个维度：

- **最大任务数**（`max_tasks`）：同时存在的活跃任务上限
- **最大上传字节数**（`max_upload_bytes`）：防止用户上传超大文件

检查时机：**写入数据库之前**就返回 402（Payment Required）（而不是写进去再删），保证数据库行数不会超限。

> 已完成（FINISHED）的任务不计入活跃任务配额。

### Q22: 数据库迁移用了什么工具？有多少张表？

用 **Alembic**（SQLAlchemy 官方迁移工具），共 14 个迁移版本：

1. `0001_initial` - 初始表（audio_tasks 等）
2. `0002_progress_fields` - 进度字段（progress, current_step）
3. `0003_add_chinese_comments` - 中文注释
4. `0004_sample_libraries` - 采样库（sample_libraries + sample_files）
5. `0005_users` - 用户表（users + user_quotas）
6. `0006_commentary` - AI 点评（commentary + commentary_model）
7. `0007_soundfonts` - 音色表（soundfonts + soundfont_presets）
8. `0008_sample_velocity_layers` - 采样力度层（velocity_min / velocity_max）
9. `0009_refresh_tokens` - Refresh Token 持久化与轮换（refresh_tokens 表）
10. `0010_sample_library_owner` - 采样库归属关系（user_id FK）
11. `0011_task_query_indexes` - 任务查询索引（status + user_id 复合索引）
12. `0012_fix_boolean_columns` - 修复布尔列默认值（PostgreSQL 兼容）
13. `0013_fk_constraints_and_indexes` - 外键约束完善 + 性能索引
14. `0014_sample_file_timestamps` - 采样文件时间戳（created_at / updated_at）

### Q23: 前端播放器是怎么调度的？

**requestAnimationFrame + Web Audio 调度**：

- 用 `requestAnimationFrame` 驱动播放头 UI 更新（60fps）
- 音频事件提前用 `AudioContext.currentTime + 时间偏移` 调度（Web Audio 的高精度时钟）
- 速度变化通过调整 playbackRate 实现

> 为什么不用 setInterval？因为 setInterval 受 JS 事件循环影响会漂移，而 Web Audio 的调度是在音频线程里做的，精度高得多。

### Q24: 采样库导出功能导出了什么？

导出一个 **JSON 格式的 GM 打击乐映射文件**，包含：

- 库元数据（名称、描述、版本）
- 格式标识（`gm_percussion_mapping`）
- 音符范围（35-81）
- 映射表：`音符 -> { label, velocity_offset, velocity_min, velocity_max, relative_path }`

用途：备份、分享自定义鼓组、导入到其他设备或软件。

---

## 四、5 大核心功能深度问答

### Q25: 功能1 - 多乐器分轨转 MIDI 的完整流程是什么？

```
音频上传
  ↓
Demucs htdemucs_6s 分离 -> vocals / drums / bass / piano / guitar / other
  ↓                                    ↓
  ↓                    instrument_classifier 对 other 二次分类
  ↓                    -> other_piano / other_guitar / other_strings
  │                      / other_synth / other_melodic
  ↓
Basic Pitch 逐轨转 MIDI：
  - drums -> drum_midi_service（19件拆分，见 Q26）
  - bass / piano / guitar / vocals -> Basic Pitch polyphonic
  - other_piano / other_guitar / ... -> Basic Pitch polyphonic
  ↓
每条 MIDI 注入 GM setup：
  - _normalize_stem_key("other_strings") -> "strings" -> program=48
  - _STEM_CC_CONFIG 查表获取 brightness/reverb/chorus
  - gm_setup_messages() 写入 CC0/CC32/Program/CC7/CC11/CC10/CC64/CC74/CC91/CC93
```

**关键设计**：`_normalize_stem_key` 会剥离 `other_` 前缀，所以 `other_strings.mid` 会使用 String Ensemble 音色（program 48）而不是 Warm Pad（program 89）。

### Q26: 功能2 - 鼓组 19 件拆分的检测逻辑是什么？

```
drums.wav
  ↓
_estimate_bpm -> BPM 自适应 onset 参数
  ↓
librosa.onset.onset_detect -> 时间戳列表
  ↓
_extract_features（每个 onset 提取 5 个特征）：
  - low_ratio / low_mid_ratio / mid_ratio / high_ratio / very_high_ratio
  - spectral_centroid / peak_freq
  - sustain_ratio（attack vs tail 能量比）
  - spectral_flux（onset 后频谱变化）
  ↓
_classify 规则引擎 -> (part, confidence)
  ↓
置信度回退：confidence < 0.55 时按 _CONFIDENCE_FALLBACK 重映射
  ↓
_derive_fills -> 密集音簇标记为 fill
  ↓
输出：drums.mid（合并）+ drums_kick.mid / drums_snare.mid / ...（19个分件）
      + drums_events.csv + drums_events.json（前端播放用）
```

**置信度回退示例**：如果 `tom_lomid` 的置信度只有 0.45，会回退到 `tom_himid`（最近的明确部件），而不是盲目接受一个可能错误的分类。

### Q27: 功能3 - GM/XG 音色映射是怎么做的？

`midi_mapping_service.py` 做了三件事：

**1. 多轨合并**：`collect_raw_midi_sources` 收集所有 raw MIDI 文件（bass.mid, piano.mid, other_strings.mid, drums.mid 等），`_dedupe_per_instrument_sources` 去重（如果有 other_piano.mid 就移除 other.mid）。

**2. 通道分配**：`_resolve_channel` 为每个 stem 分配不冲突的 MIDI 通道。drums 固定 channel 9，其他按 `_BUILTIN_VOICES` 的默认通道分配，冲突时自动找空闲通道。

**3. Profile 映射**：对每个 stem 写入 setup track：

- GM profile：GM System On SysEx + Bank 0:0 + Program + CC7/10/11
- XG profile：XG System On SysEx + XG drum bank (127:0) 或 XG melodic variation (0:1) + Program + CC7/10/11

**XG 旋律变奏**（`_BUILTIN_XG_MELODIC_VOICES`）：

- piano -> "Live! Grand Piano" (bank 0:1)
- guitar -> "Nylon Guitar" (bank 0:1)
- strings -> "Stereo Strings" (bank 0:1)
- bass/synth/other -> 无变奏，使用 GM 兼容音色 (bank 0:0)

**SoundFont 覆盖**：如果用户激活了 SoundFont，`build_soundfont_overrides` 会为每个 stem 找到最接近的预设，替换默认 GM 音色。

### Q28: 功能4 - MIDI 控制器兼容是怎么实现的？

三层兼容：

**1. GM Setup（`midi_cc.py` -> `gm_setup_messages`）**：
每个 note track 头部写入完整控制器序列：CC0(Bank MSB) -> CC32(Bank LSB) -> Program Change -> CC7(Volume) -> CC11(Expression) -> CC10(Pan) -> CC64(Sustain) -> CC74(Brightness) -> CC91(Reverb) -> CC93(Chorus) -> CC1(Modulation)

**2. Per-stem 控制器配置（`_STEM_CC_CONFIG`）**：
每种乐器有不同的默认控制器值：

- piano: brightness=64, reverb=40, chorus=0
- bass: brightness=64, reverb=15, chorus=0
- strings: brightness=72, reverb=55, chorus=35
- synth: brightness=80, reverb=40, chorus=25

**3. Velocity 兼容**：

- 旋律轨：Basic Pitch 输出原始 velocity，librosa fallback 用 `velocity_from_strength` sqrt 曲线
- 鼓组：95th 百分位参考 + sqrt 曲线，保留动态范围
- 采样播放：前端按 velocity 选择对应力度层的采样（`velocity_min` / `velocity_max`）

**4. Pitch Bend 复位**：
每个音符前插入 `pitchwheel=0` 消息，防止前一音符的 bend 值泄漏。

### Q29: 功能5 - 自定义采样库 + 自动识别 + 电子琴音色表怎么实现？

**三层识别策略**（按优先级）：

**1. 文件名映射**（`_resolve_note_from_name`）：
60+ 别名表覆盖 Roland/Yamaha 命名惯例：

- `kick` / `bd` / `bass_drum` / `kik` -> note 36
- `snare` / `snr` / `sd` -> note 38
- `closed_hat` / `chh` / `hhc` -> note 42
- 支持尾部数字剥离：`kick_01` -> `kick` -> 36
- 支持 token 匹配：`studio_kick` -> 匹配 `kick` -> 36

**2. 频谱自动识别**（`sample_classifier_service.py`）：
文件名匹配失败时，通过音频内容分类：

- 提取 centroid / peak_freq / rolloff / ZCR / harmonicity / attack_ratio
- 多候选规则引擎 -> 取最高置信度
- 识别 kick / snare / hihat / tom / cymbal / percussion 等 30+ 种

**3. Velocity 层解析**（`_resolve_velocity_range`）：
从文件名解析力度层：

- `kick_vel_001_064.wav` -> (1, 64)
- `snare_v51-100.wav` -> (51, 100)
- `crash_pp.wav` -> (1, 42)
- `snare_hard.wav` -> (64, 127)

**电子琴音色表导入**：
CSV 格式（`/api/instruments/preset-table/import`）：

```csv
bank_msb,bank_lsb,program,name,category,instrument_type
0,0,0,Grand Piano,Piano,piano
0,1,0,Live! Grand Piano,Piano,piano
0,0,24,Nylon Guitar,Guitar,guitar
```

**GM -> 自定义映射**（`map_gm_to_custom`）三级匹配：

1. instrument_type 精确匹配（"piano" -> 找 instrument_type="piano" 的预设）
2. program 精确匹配（GM program 0 -> 自定义 program 0）
3. 名称 token 模糊匹配（Jaccard 相似度 >= 0.4）

---

## 五、面试高频追问方向

### Q30: 如果让你优化性能，你会从哪入手？

1. **Demucs 推理加速**：用 ONNX Runtime 或 TensorRT 优化，或换更轻量的模型（如 htdemucs_ft）
2. **缓存**：相同文件上传直接复用结果（用文件 hash 去重），避免重复跑整个 pipeline
3. **采样分类**：当前是规则引擎，精度有限；可引入轻量 ML 模型（如 MobileNet 分类器）
4. **前端播放**：提前预解码 AudioBuffer，播放时零延迟；使用 Web Worker 避免主线程阻塞
5. **数据库**：常用查询加覆盖索引，大结果集使用游标分页代替 OFFSET
6. **Worker 调度**：根据音频时长动态调整 concurrency，短音频多并发、长音频少并发避免 OOM

### Q31: 这个项目的难点是什么？

1. **音频处理流水线长**：分离 -> 分类 -> 转 MIDI -> 映射 -> 分析，每一步都可能失败，需要完善的错误处理和 fallback
2. **多轨 MIDI 一致性**：各轨要对齐到同一时间轴，CC 控制器要按 stem 类型正确设置，通道不能冲突
3. **实时性与准确性的权衡**：乐器分类用规则引擎而不是深度学习，就是为了快和无依赖
4. **浏览器音频播放精度**：Web Audio API 的调度和缓存策略，velocity 层选择
5. **GM/XG 标准兼容**：两种标准的 Bank Select、SysEx、通道分配规则不同，需要同时生成两个变体

### Q32: 你在项目中做了哪些取舍（trade-off）？

| 决策      | 选择          | 放弃了什么                | 为什么                               |
| --------- | ------------- | ------------------------- | ------------------------------------ |
| 分离模型  | Demucs 6-stem | 自训练模型                | 6-stem 已覆盖钢琴/吉他，自训练成本高 |
| 乐器分类  | 规则引擎      | 深度学习模型              | 无外部依赖，快速，可解释             |
| 鼓组检测  | 频谱规则      | ADT 深度学习              | 默认无依赖；ADTOS 作为可选后端       |
| MIDI 转录 | Basic Pitch   | 自训练模型                | Spotify 开源，polyphonic，够用       |
| 前端播放  | Web Audio API | MIDI.js / Tone.js         | 原生 API 零依赖，精度高              |
| 异步任务  | Celery        | asyncio + BackgroundTasks | CPU-bound 任务需要独立进程           |
| 数据库    | PostgreSQL 16 | SQLite                    | 并发支持、JSON 类型、FK 约束、生产级可靠性 |
| 部署      | Docker Compose | Kubernetes               | 项目规模适中，Compose 足够简单可靠   |

### Q33: 项目有多少测试？覆盖了什么？

**279 个后端测试**（pytest，22 个测试文件），覆盖：

- Service 层逻辑（Demucs / Basic Pitch / Drum MIDI / MIDI Mapping / Sample Library / SoundFont / Sample Classifier / Music Analysis / LLM / File / ADT Drum / Task）
- API 路由（audio / tasks / instruments / auth / ws / health / rate limit）
- MIDI 操作（GM setup / CC injection / XG mapping / channel allocation / drum MIDI split）
- Auth（JWT / refresh rotation / quota / user management）
- WebSocket（connection / progress / fallback）
- 安全（security regression / rate limiting）
- 边界情况（空音频 / 静音文件 / 无效文件名 / 超大文件）

**前端**：TypeScript 严格编译 + 11 个 Vitest 单元测试 + Playwright E2E（upload -> process -> detail 完整流程）

### Q34: 如果用户上传的音频质量很差（噪音多），系统会怎样？

1. **Demucs 分离**：分离质量下降，stem 之间可能有串音（bleed），但不会报错
2. **乐器分类**：噪音会影响频谱特征，flatness 升高可能导致更多内容被归为 synth 或 other_melodic
3. **鼓组检测**：onset 检测可能把噪音误判为 hit，但置信度回退机制会把低置信度的 hit 重映射到最近的明确部件
4. **Basic Pitch**：噪音可能产生幽灵音符，但 onset_threshold 和 min_note_length 参数会过滤掉大部分
5. **整体**：pipeline 每一步都有 try/except，单步失败不会导致整个任务崩溃，会 fallback 到合理默认值

---

## 六、关键代码位置速查

| 功能               | 后端位置                                      | 前端位置                                 |
| ------------------ | --------------------------------------------- | ---------------------------------------- |
| 6 轨分离           | `services/demucs_service.py`                | -                                        |
| 乐器分类           | `services/instrument_classifier_service.py` | -                                        |
| MIDI 转录          | `services/basic_pitch_service.py`           | -                                        |
| 鼓组 19 件拆分     | `services/drum_midi_service.py`             | -                                        |
| GM/CC 设置         | `services/midi_cc.py`                       | -                                        |
| GM/XG 映射         | `services/midi_mapping_service.py`          | -                                        |
| 采样自动识别       | `services/sample_classifier_service.py`     | -                                        |
| 采样库管理         | `services/sample_library_service.py`        | `pages/SampleLibraryPage.tsx`          |
| SoundFont/CSV 导入 | `services/soundfont_service.py`             | `pages/SampleLibraryPage.tsx`          |
| 音乐分析           | `services/music_analysis_service.py`        | `pages/AudioDetailPage.tsx`            |
| 浏览器播放         | -                                             | `components/SampleBasedDrumPlayer.tsx` |
| WebSocket 进度     | `api/ws.py`                                 | `hooks/useTaskProgress.ts`             |
| 用户认证           | `services/auth_service.py`                  | `contexts/AuthContext.tsx`             |
| 定时任务           | `tasks_scheduled.py`                        | -                                        |
| Pipeline 编排      | `workers/audio_worker.py`                   | -                                        |
| Celery 配置        | `celery_app.py`                             | -                                        |
| 任务状态管理       | `services/task_service.py`                  | -                                        |
| CI/CD              | `.github/workflows/{ci,cd,e2e,security-scan}.yml` | -                                   |

---
## 七、DevOps 与运维篇

### Q35: 项目有哪些 CI/CD 工作流？各做什么？

**4 个 GitHub Actions 工作流**：

| 工作流 | 触发条件 | 作用 |
| ------ | -------- | ---- |
| `ci.yml` | push / PR 到 main | 运行后端 pytest 279 测试 + 前端 Vitest + TypeScript 编译检查 |
| `cd.yml` | push 到 main 或 tag v* | 构建多阶段 Docker 镜像，推送到 GitHub Container Registry (GHCR)，tag 版本产生不可变镜像 |
| `e2e.yml` | push / PR 到 main | Playwright 端到端测试：上传音频 -> 等待处理 -> 验证详情页，使用 PostgreSQL 16 服务容器 |
| `security-scan.yml` | push / PR 到 main + 每周一 8:00 | CodeQL 安全扫描，发现漏洞后写入 Security Events |

> CI 使用 concurrency 控制避免重复运行，E2E 和 Security Scan 有 `workflow_dispatch` 支持手动触发。

### Q36: Worker 崩溃后任务怎么恢复？有什么防护措施？

多层防护：

1. **CAS 原子认领**（`worker_claim`）：用 SQL UPDATE + WHERE 条件做 Compare-And-Swap，防止两个 Worker 同时处理同一任务
2. **35 分钟超时恢复**：如果任务在 PROCESSING 状态超过 35 分钟（Celery `task_time_limit` 30 分钟 + 5 分钟缓冲），`worker_claim` 的 WHERE 条件会允许重新认领，回收崩溃 Worker 留下的任务
3. **Celery `acks_late=True`**：Worker 崩溃后消息自动重新入队，不会丢失
4. **内存保护**（`worker_limits.py`）：处理前检查 RSS 内存，超过 `WORKER_MEMORY_GATE_MB` 阈值时拒绝执行并重新入队，防止 OOM 连锁崩溃
5. **`max_tasks_per_child=10`**：Worker 子进程每处理 10 个任务后自动重启，防止内存泄漏累积
6. **`max_memory_per_child=3500MB`**：子进程内存超限时自动回收

> 在 macOS 上还需设置 `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` 环境变量，因为 Celery 的 prefork 模型在 macOS 上会因 ObjC 运行时不是 fork-safe 而 SIGABRT 崩溃。

### Q37: Docker Compose 部署包含哪些服务？怎么保证安全？

**4 个服务**：

| 服务 | 镜像 | 说明 |
| ---- | ---- | ---- |
| `postgres` | `postgres:16-alpine` | 数据库，仅 compose 内网可达，健康检查用 `pg_isready` |
| `redis` | `redis:7-alpine` | 消息队列 + 缓存 + PubSub，不暴露端口 |
| `api` | 多阶段构建 | FastAPI + Uvicorn，非 root 用户运行 |
| `worker` | 多阶段构建 | Celery Worker + Beat，非 root 用户运行 |

**安全措施**：
- 所有容器以非 root 用户运行（`USER 1000`）
- PostgreSQL 和 Redis 不暴露宿主机端口（仅 compose 内网可达）
- 敏感信息通过环境变量注入（`.env` 文件），不在镜像中硬编码
- Docker 镜像使用多阶段构建减小体积（最终镜像不含编译工具链）
- API 带 rate limiting、CORS 白名单、CSP 安全头

---
## 八、建议重点掌握

面试最常问的方向：

1. **GM/XG 标准** - Program Change、Bank Select、打击乐通道、XG 变奏
2. **MIDI CC 控制器** - 常用 CC 号及其作用、per-stem 差异化配置
3. **鼓组映射** - GM 打击乐音符分配、19 个鼓部件、fill 检测、置信度回退
4. **6 轨分离原理** - Demucs htdemucs_6s、二级乐器分类、soft mask 重建
5. **Web Audio 播放机制** - AudioBuffer、调度方式、力度控制、velocity 层
6. **系统架构** - 前后端分离 + Celery 异步任务 + WebSocket 实时推送 + Celery Beat 定时任务
7. **自定义采样** - 文件名映射、频谱自动识别、SF2/CSV 导入、GM->自定义三级匹配
8. **工程规范** - 279 测试、14 个 Alembic 迁移、4 个 CI/CD 工作流、Docker 非 root 运行、pre-commit hooks

---

> **面试技巧**：重点理解数据流、架构设计决策、以及为什么选这些技术而不是替代方案。面试官通常更关心你如何思考和权衡，而不是纯背诵。遇到不确定的问题，诚实说"这里我用了 X 方案，因为 Y，但 Z 方案可能更好，只是当时考虑到 W"，比硬编答案强得多。
