# 🎵 music-ai 项目面试知识点（问答版）

## 一、项目整体

### Q1: 这个项目是做什么的？核心功能有哪些？
这是一个 **AI 音乐处理 Web 应用**，用户上传音频后，系统自动完成：

1. **音源分离** - 用 Demucs 分离 vocals/drums/bass/other 四轨
2. **乐器识别与 MIDI 转录** - 从 other 轨二次识别钢琴/吉他/弦乐等，用 Basic Pitch 转成 MIDI
3. **鼓组精细拆分** - 把鼓 MIDI 拆成 19 个独立部件（底鼓、军鼓、镲片等）
4. **GM/XG 音色映射** - 生成的 MIDI 符合 GM/XG 标准，可在任意 DAW 中正确播放
5. **自定义采样库** - 用户可上传自己的鼓采样，浏览器端用 Web Audio API 播放
6. **音乐分析** - BPM、调式、和弦、段落检测 + AI 点评
7. **用户系统** - JWT 认证、配额管理、数据隔离
8. **实时进度** - WebSocket 推送处理进度

技术栈：**FastAPI + Celery + PostgreSQL + Redis + React/Vite + Web Audio API**

---

## 二、音乐知识篇

### Q2: 什么是 GM（General MIDI）？为什么要兼容它？
**GM 是通用 MIDI 标准**，规定了 128 种乐器的编号映射和 47 个打击乐音符映射。

- **旋律声部**：Program Change 0-127 对应 128 种乐器（0=大钢琴，24=尼龙吉他，40=小提琴…）
- **打击声部**：固定使用 **第 10 通道（Channel 9，从 0 开始数）**，音符 35-81 对应不同鼓件
- **意义**：保证同一个 MIDI 文件在任何支持 GM 的音源/合成器/DAW 上听起来都差不多

> 项目中所有生成的 MIDI 都带有完整的 GM setup sequence（Bank MSB/LSB → Program Change → CC7 音量 → CC11 表情 → CC10 声像），确保在任意 DAW 中音色正确。

### Q3: 什么是 XG？和 GM 是什么关系？
**XG 是 Yamaha 提出的 GM 扩展标准**，在 GM 基础上增加了更多音色和效果。

- 通过 **Bank Select（CC0 + CC32）** 切换不同音色库
- GM 用 Bank 0:0，XG 用 Bank 121:0
- 鼓组也可以通过 Bank 切换（标准鼓、爵士鼓、电子鼓等）

> 项目中 `midi_cc.py` 的 `gm_setup_messages()` 函数统一构建 setup sequence，支持通过参数切换 GM/XG bank。

### Q4: MIDI CC 控制器是什么？项目中用到了哪些 CC？
**CC（Control Change）** 是 MIDI 协议中用于控制音色参数的消息，范围 0-127。

项目中用到的 CC：

| CC 号 | 名称 | 作用 | 默认值 |
|-------|------|------|--------|
| CC 0 | Bank MSB | 选择音色库（高位） | GM=0, XG=121 |
| CC 32 | Bank LSB | 选择音色库（低位） | 0 |
| CC 7 | Channel Volume | 通道音量 | 100 |
| CC 10 | Pan | 声像（左右声道） | 64（居中） |
| CC 11 | Expression | 表情控制器（精细音量） | 127 |
| CC 64 | Sustain Pedal | 延音踏板 | 0（关闭） |

> 延音踏板（CC64）在长和弦开始时下发 on，结束时下发 off，模拟钢琴踩踏板效果。

### Q5: 什么是 Program Change？和 Bank Select 什么关系？
- **Program Change**：在当前 Bank 内选择具体乐器（0-127）
- **Bank Select**：先选库，再选具体音色
- **顺序**：先发 Bank MSB (CC0) → Bank LSB (CC32) → 再发 Program Change

> 为什么要分两步？因为 128 个音色不够用，用 Bank 可以扩展到 128×128=16384 个音色。

### Q6: 鼓组 MIDI 为什么用第 10 通道？
这是 GM 标准的规定：**通道 9（第 10 通道，从 0 开始）是打击乐专用通道**。

- 在这个通道上，Note On 的音符号不代表音高，而代表**不同的鼓件**
- 比如：36=底鼓(Bass Drum 1)、38=军鼓(Snare Drum 1)、42=闭合踩镲(Closed Hi-Hat)、49=坠镲(Crash Cymbal 1)

> 项目中鼓 MIDI 全部写在通道 9 上，每个部件对应一个 GM 标准音符。

### Q7: 项目中的 19 个鼓部件分别是什么？
19 个 GM 打击乐部件：
`kick`(底鼓), `snare`(军鼓), `sidestick`(边击), `hihat_closed`(闭合踩镲), `hihat_open`(打开踩镲), `tom_high`(高音通鼓), `tom_himid`, `tom_lomid`, `tom_low`, `tom_floor`(落地通鼓), `crash`(坠镲), `ride`(叮叮镲), `china`(中国镲), `splash`(水镲), `ride_bell`(镲帽), `tambourine`(铃鼓), `cowbell`(牛铃), `percussion`(打击乐), `fill`(加花)

> 其中 `fill`（加花）是后处理推导的：把时间上挨得很近的密集击打点归为一组 fill。

### Q8: 什么是音源分离（Source Separation）？项目用了什么模型？
**音源分离**就是从一首混音歌曲中分离出各个乐器的音轨。

项目用 **Demucs 模型**，分离 4 个音轨：
- **vocals**（人声）
- **drums**（鼓）
- **bass**（贝斯）
- **other**（其他所有，包括钢琴、吉他、弦乐等）

> 项目进一步用 `instrument_classifier_service.py` 对 other 轨做**二次分类**，识别钢琴、吉他、弦乐、合成器等，再分别转 MIDI。

### Q9: 乐器分类是怎么做的？用了什么特征？
用**基于规则的频谱特征分类**，不依赖外部模型，轻量快速。

核心特征：
- **频谱质心（Spectral Centroid）** - 音色"亮"不亮
- **频谱带宽（Bandwidth）** - 频率分布宽度
- **频谱滚降（Rolloff）** - 能量集中在低频还是高频
- **频谱平坦度（Flatness）** - 像噪音还是像乐音
- **过零率（ZCR）** - 信号穿过零点的频率
- **低/中/高频能量比**
- **谐波性（Harmonicity）** - 是否有明显的音高

> 对每帧计算特征，用**启发式规则表**判断乐器类型。优点是快、无依赖、可解释；缺点是精度不如深度学习模型。

### Q10: 采样分类（Sample Classifier）是怎么识别鼓采样的？
和乐器分类思路类似，针对鼓采样的特点：

1. **提取频谱特征**：质心、峰值频率、能量比、过零率
2. **规则引擎判断**：
   - 低频能量多、峰值频率低 → 底鼓(kick)
   - 中频为主、有噪声成分 → 军鼓(snare)
   - 高频、持续时间短 → 踩镲(hi-hat)
   - 高频、衰减慢 → 镲片(cymbals)
3. **映射到 GM 音符**：根据分类结果分配标准 GM 打击乐音符

> 用户上传任意命名的采样文件，系统通过音频内容自动识别类型并分配正确的 MIDI 音符。

### Q11: 什么是 Basic Pitch？它和传统音高检测有什么区别？
**Basic Pitch** 是 Spotify 开源的**多音高检测（polyphonic pitch detection）模型**，可以从音频中转出复音 MIDI。

区别于传统方法（如 autocorrelation、FFT 峰值检测）：
- 传统方法大多只能检测**单音**（monophonic）
- Basic Pitch 用深度学习，可以检测**和弦**等复音
- 输出带力度（velocity）信息

> 项目中 `basic_pitch_service.py` 调用 Basic Pitch 把各乐器音轨转成 MIDI。

### Q12: 力度（Velocity）是怎么计算的？
力度范围 0-127，项目用 `velocity_from_strength()` 函数计算：

- 输入：信号强度（0-1）
- 用 **平方根曲线** 映射（人耳对响度的感知是非线性的，接近对数/平方根关系）
- 裁剪到 **35-127** 范围（避免太弱的音符）

> 为什么用平方根？因为人耳对声音响度的感知是**对数级**的，线性映射会觉得"中间区域变化不明显"，用曲线可以让力度变化更自然。

### Q13: 什么是 SoundFont（SF2）？
**SoundFont** 是一种采样乐器格式（.sf2 文件），包含：
- 多个 **Preset（预设）** - 每个预设对应一种音色
- 每个预设由多个 **Sample（采样）** 组成，按音高和力度分层
- 包含包络（ADSR）、滤波、颤音等合成参数

> 项目中可以导入 SF2 文件或 CSV 格式的预设表，管理音色库并用于 GM/XG 映射。

### Q14: BPM 和调式（Key）是怎么检测的？
- **BPM 检测**：通过 onset detection（起始点检测）分析节拍间隔，用频谱分析或自相关找周期性
- **调式检测**：分析音高分布，对照大/小调的音程模式计算匹配度
- **和弦检测**：基于音高类分布（pitch class profile）匹配和弦模板

> 这些都在 `music_analysis_service.py` 中，用 librosa 库实现。

### Q15: Web Audio API 播放采样的原理是什么？
浏览器端播放鼓采样的流程：

1. **加载**：`fetch()` 获取音频文件 → `AudioContext.decodeAudioData()` 解码成 `AudioBuffer`
2. **缓存**：解码后的 AudioBuffer 存在内存里，避免重复解码
3. **调度**：`AudioBufferSourceNode` 调度播放 → `GainNode` 控制力度 → 连接到 `destination`
4. **时间精度**：用 `AudioContext.currentTime + offset` 精确调度（比 setTimeout 准得多）
5. **力度控制**：通过 GainNode 的 gain 值实现 velocity 效果

> 项目中 `SampleBasedDrumPlayer.tsx` 和 LibraryCard 的预览播放都用了这个机制。

---

## 三、业务/技术知识篇

### Q16: 项目的技术架构是什么？
**前后端分离 + 异步任务处理**架构：

```
前端 (React/Vite)
    ↓ HTTP/WebSocket
API 层 (FastAPI)
    ↓ 
数据库 (PostgreSQL) + 缓存/消息队列 (Redis)
    ↓ Celery
异步 Worker (Demucs + Basic Pitch + 各种 Service)
```

- **同步**：用户上传、查询等请求直接由 FastAPI 处理
- **异步**：音频处理耗时，通过 Celery 后台执行，WebSocket 推送进度

### Q17: 为什么用 Celery？可以不用吗？
因为音频处理（Demucs 分离、Basic Pitch 转 MIDI）是**计算密集型任务**，可能需要几十秒到几分钟：
- 同步处理会导致 HTTP 请求超时
- Celery + Redis/Broker 可以后台异步执行，前端通过轮询或 WebSocket 看进度
- 支持任务队列、重试、并发控制

> 项目中 `audio_worker.py` 就是 Celery worker，处理流程是一个 pipeline：上传 → 分离 → 乐器分类 → 转 MIDI → 分析 → 完成。

### Q18: 采样库的"单激活"（一个用户只能有一个活跃库）是怎么实现的？
用 **部分唯一索引（Partial Unique Index）** 保证数据一致性：

- 数据库层：在 `sample_libraries` 表上建一个 `WHERE is_active = 1` 的唯一索引
- 应用层：激活新库时，在一个事务里把旧库的 is_active 清 0，新库设 1
- 为什么数据库层也要保证？防止应用层有 bug 或并发操作导致多个激活库

### Q19: WebSocket 实时进度是怎么实现的？
经典的 **发布-订阅（Pub/Sub）模式**：

1. Worker 每完成一个步骤，就往 Redis 的 `task:{id}` channel 发一条进度消息
2. WebSocket 客户端连接后，先从 DB 拿当前状态（snapshot），然后订阅 channel
3. 收到新消息就推给前端，前端直接更新 React Query 缓存
4. 任务结束（FINISHED/FAILED）后关闭连接

**降级策略**：Redis 不可用时，自动退化为 1 秒轮询 DB。

### Q20: JWT 认证是怎么做的？有什么安全措施？
**HS256 算法**，双 token 机制：
- **Access Token**：24 小时有效期，用于 API 调用
- **Refresh Token**：30 天有效期，用于换新的 access token
- **Refresh Token 轮换**：每次用 refresh token 换新 token 时，refresh token 也会变（防止重放）
- **类型区分**：access token 不能当 refresh token 用，反之亦然（payload 里有 type 字段）

密码用 **bcrypt** 哈希（passlib 库）。

### Q21: 用户配额（Quota）是怎么实现的？
两个维度：
- **最大任务数**（`max_tasks`）：同时存在的活跃任务上限
- **最大上传字节数**（`max_upload_bytes`）：防止用户上传超大文件

检查时机：**写入数据库之前**就返回 429（而不是写进去再删），保证数据库行数不会超限。

> 已完成（FINISHED）的任务不计入活跃任务配额。

### Q22: 数据库迁移用了什么工具？有多少张表？
用 **Alembic**（SQLAlchemy 官方迁移工具），共 7 个迁移版本：

1. `0001_initial` - 初始表（audio_tasks 等）
2. `0002_progress_fields` - 进度字段
3. `0003_add_chinese_comments` - 中文注释
4. `0004_sample_libraries` - 采样库（sample_libraries + sample_files）
5. `0005_users` - 用户表
6. `0006_commentary` - AI 点评
7. `0007_soundfonts` - 音色表（soundfonts + soundfont_presets）

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
- 映射表：`音符 → { label, velocity_offset, relative_path }`

用途：备份、分享自定义鼓组、导入到其他设备或软件。

---

## 四、面试高频追问方向

### Q25: 如果让你优化性能，你会从哪入手？
1. **Demucs 推理加速**：用 ONNX Runtime 或 TensorRT 优化，或者换更轻量的模型
2. **缓存**：相同文件上传直接复用结果（用文件 hash 去重）
3. **采样分类**：当前是规则引擎，精度有限；可以引入轻量 ML 模型
4. **前端播放**：提前预解码 AudioBuffer，播放时零延迟
5. **数据库**：常用查询加索引，分页优化

### Q26: 这个项目的难点是什么？
- **音频处理流水线长**：分离 → 分类 → 转 MIDI → 分析，每一步都可能失败，需要完善的错误处理和重试
- **实时性与准确性的权衡**：乐器分类用规则引擎而不是深度学习，就是为了快
- **多轨 MIDI 的一致性**：各轨要对齐到同一时间轴，CC 控制器要正确设置
- **浏览器音频播放精度**：Web Audio API 的调度和缓存策略

---

## 五、关键代码位置速查

| 功能 | 后端位置 | 前端位置 |
|------|---------|---------|
| 音源分离 | `app/workers/audio_worker.py` | - |
| 乐器分类 | `app/services/instrument_classifier_service.py` | - |
| MIDI 转录 | `app/services/basic_pitch_service.py` | - |
| 鼓组拆分 | `app/services/drum_midi_service.py` | - |
| GM/CC 设置 | `app/services/midi_cc.py` | - |
| 采样分类 | `app/services/sample_classifier_service.py` | - |
| 采样库管理 | `app/services/sample_library_service.py` | `pages/SampleLibraryPage.tsx` |
| SoundFont 管理 | `app/services/soundfont_service.py` | `pages/SampleLibraryPage.tsx` |
| 音乐分析 | `app/services/music_analysis_service.py` | `pages/AudioDetailPage.tsx` |
| 浏览器播放 | - | `components/SampleBasedDrumPlayer.tsx` |
| WebSocket 进度 | `app/api/ws.py` | `hooks/useTaskProgress.ts` |
| 用户认证 | `app/services/auth_service.py` | - |

---

## 六、建议重点掌握

面试最常问的方向：
1. **GM/XG 标准** - Program Change、Bank Select、打击乐通道
2. **MIDI CC 控制器** - 常用 CC 号及其作用
3. **鼓组映射** - GM 打击乐音符分配、19 个鼓部件
4. **音源分离原理** - Demucs 四轨分离、二次乐器分类
5. **Web Audio 播放机制** - AudioBuffer、调度方式、力度控制
6. **系统架构** - 前后端分离 + Celery 异步任务 + WebSocket 实时推送
