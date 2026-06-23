# 短视频 AI 内容工厂

一个面向短视频运营的 AI 内容生产系统。系统通过分析对标博主视频，蒸馏内容风格与爆款结构，再自动生成原创口播文案、合成语音、渲染字幕，并产出适合抖音 / 小红书 / 快手的竖屏 MP4 视频。

## 系统架构总览

```mermaid
graph TB
    subgraph Frontend[" 前端 - React "]
        F1[博主管理] --- F2[内容日历] --- F3[视频库] --- F4[系统设置]
    end

    subgraph Backend[" 后端 - Python FastAPI "]
        B1[博主蒸馏: yt-dlp + Whisper + Claude]
        B2[文案生成: Claude API]
        B3[TTS: Edge-TTS 语音合成]
        B4[视频合成: FFmpeg + ASS 字幕]
    end

    subgraph Queue[" 任务队列 "]
        Q[Celery + Redis 异步调度]
    end

    subgraph Storage[" 数据与存储层 "]
        D[(SQLite 数据库)]
        S[(本地文件存储)]
    end

    Frontend -->|REST API| Backend
    Backend --> Queue
    Queue --> Storage
</mermaid>

## 项目定位

本项目的目标不是简单生成一段文案，而是搭建一条完整的短视频生产流水线：

1. 添加对标博主或源视频
2. 下载视频并提取音频
3. 使用 Whisper 转写视频内容
4. 使用 Claude 蒸馏内容基因
5. 基于内容基因生成原创文案
6. 使用系统音色或克隆音色合成口播
7. 使用用户照片生成数字人口播画面
8. 渲染移动端友好的字幕
9. 输出 1080×1920 竖屏短视频

## 核心能力

- **博主内容蒸馏**：分析价值定位、爆款技巧、选题偏好和话术风格。
- **原创文案生成**：基于内容基因生成新的短视频选题和口播稿。
- **个人信息过滤**：避免复用原博主姓名、账号名、自我介绍、感谢观看等个人标识内容。
- **TTS 语音合成**：内置 Edge-TTS 中文音色，支持后续接入声音克隆引擎。
- **声音样本管理**：支持上传并保存用户声音样本，方便后续复用。
- **数字人口播视频**：使用用户照片或默认数字人形象生成口播视频画面。
- **移动端字幕渲染**：按自然停顿切分字幕，去掉字幕末尾标点，适配短视频观看体验。
- **内容日历**：管理待审核、已通过、已驳回、生成中和已生成视频的文案。
- **垃圾站机制**：不好的文案可删除到垃圾站，支持恢复、彻底删除和一键清空。
- **视频库**：查看、预览、下载已生成的视频。

## 技术栈

| 层级 | 技术 | 说明 |
| --- | --- | --- |
| 前端 | React + Vite + TailwindCSS | 管理后台、内容日历、视频库、系统设置 |
| 后端 | FastAPI + SQLAlchemy | REST API、数据模型、业务服务 |
| 异步任务 | Celery + Redis | 视频下载、转写、蒸馏、视频生成等耗时任务 |
| 数据库 | SQLite | 本地开发和单机部署使用 |
| AI 能力 | Claude API | 内容蒸馏与原创文案生成 |
| 语音转文字 | faster-whisper | 本地音频转写 |
| 视频下载 | yt-dlp | 下载对标视频 |
| 语音合成 | Edge-TTS | 中文 TTS 合成 |
| 视频合成 | FFmpeg | 音频、画面、字幕合成为 MP4 |
| 画面生成 | Pillow + NumPy | 生成数字人展示场景 |

## 系统流程

```mermaid
flowchart TD
  A[添加对标博主 / 视频] --> B[下载视频]
  B --> C[提取音频]
  C --> D[Whisper 转写]
  D --> E[Claude 蒸馏内容基因]
  E --> F[生成原创文案]
  F --> G[内容日历审核]
  G --> H[TTS / 克隆音色合成语音]
  H --> I[生成字幕时间轴]
  I --> J[数字人口播画面合成]
  J --> K[输出竖屏 MP4]
```

## 架构概览

```text
frontend/
  React + Vite + TailwindCSS
  页面：Dashboard / Bloggers / ContentCalendar / VideoLibrary / Settings

backend/
  FastAPI REST API
  SQLAlchemy Models
  Celery Tasks
  Services:
    - distill_service.py
    - script_generator.py
    - tts_service.py
    - video_composer.py
    - video_downloader.py
    - whisper_service.py

storage/
  source videos
  transcripts
  generated audio
  generated videos
  user photos
  voice samples
```

## 目录结构

```text
distill-skill/
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   ├── routers/
│   ├── services/
│   ├── tasks/
│   ├── scheduler.py
│   ├── requirements.txt
│   └── storage/
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── pages/
│   │   └── App.jsx
│   ├── package.json
│   └── vite.config.js
├── docker-compose.yml
└── README.md
```

## 数据模型

| 表 | 作用 |
| --- | --- |
| `bloggers` | 保存对标博主信息与分析状态 |
| `source_videos` | 保存源视频、音频、转写文本和元数据 |
| `blogger_content_dna` | 保存蒸馏后的内容基因 |
| `generated_scripts` | 保存生成的文案、审核状态和垃圾站状态 |
| `generated_videos` | 保存生成视频、音频、字幕和状态 |
| `user_profile` | 保存用户照片、系统音色、声音样本和视频配置 |

## API 模块

| 模块 | 主要接口 |
| --- | --- |
| 博主管理 | `GET /api/bloggers`、`POST /api/bloggers`、`POST /api/bloggers/{id}/analyze` |
| 源视频 | `POST /api/bloggers/{id}/videos`、`POST /api/videos/{id}/transcribe` |
| 文案 | `GET /api/scripts`、`PATCH /api/scripts/{id}`、`POST /api/scripts/generate` |
| 审核 | `POST /api/scripts/{id}/approve`、`POST /api/scripts/{id}/reject` |
| 垃圾站 | `DELETE /api/scripts/{id}`、`POST /api/scripts/{id}/restore`、`DELETE /api/scripts/trash/empty` |
| 视频生成 | `POST /api/scripts/{id}/generate-video`、`GET /api/videos-generated` |
| 设置 | `GET /api/settings`、`PUT /api/settings`、`POST /api/settings/upload-photo`、`POST /api/settings/upload-voice-sample` |

## 字幕设计

字幕按短视频移动端体验设计：

- 按自然停顿切分字幕。
- 逗号、分号、冒号、句号、问号、感叹号可以作为字幕停顿点。
- 顿号不作为字幕切分点，避免列表内容被切得太碎。
- 字幕显示时去掉末尾标点。
- 单条字幕保持短行展示，避免长句一次性铺满屏幕。
- 字幕时间轴来自分段 TTS 音频的真实时长，保证口播和字幕对齐。

## 声音与数字人

系统默认使用 Edge-TTS 中文音色，也保留系统音色列表供用户选择。用户可以上传自己的声音样本，后续通过 `VOICE_CLONE_COMMAND` 接入本地或外部声音克隆服务。

```bash
VOICE_CLONE_COMMAND='python clone_tts.py --ref {sample_path} --text {text_file} --out {output_path}'
```

当未配置声音克隆命令时，系统会自动回退到 Edge-TTS。

## 本地运行

### 1. 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8002
```

### 2. Celery Worker

```bash
cd backend
celery -A tasks.celery_app worker -l info -c 1
```

### 3. 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认运行在：

```text
http://localhost:3000
```

后端 API 默认代理到：

```text
http://localhost:8002
```

## Docker 运行

```bash
docker compose up --build
```

服务端口：

| 服务 | 端口 |
| --- | --- |
| Frontend | `3000` |
| Backend | `8000` |
| Redis | `6379` |

## 环境变量

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | 是 | Claude API Key，用于蒸馏和文案生成 |
| `REDIS_URL` | 否 | Redis 地址，默认本地 Redis |
| `VOICE_CLONE_COMMAND` | 否 | 声音克隆命令模板 |

## 质量与安全约束

- 生成文案必须是原创内容，不照搬源视频原文。
- 蒸馏阶段只保留可泛化的方法论和表达结构。
- 不复用原博主姓名、账号名、地域身份、工作学校、家庭成员、联系方式等个人信息。
- 不生成“我是 XXX”“感谢大家观看”“关注 XXX”等容易冒充原博主的署名式表达。
- 字幕和口播必须对齐，不能出现大段字幕长时间停留。

## 验证方式

```bash
# 后端语法检查
python -m py_compile backend/routers/scripts.py

# 前端构建
npm --prefix frontend run build
```

端到端验证流程：

1. 添加博主或源视频。
2. 触发转写和蒸馏。
3. 生成原创文案。
4. 在内容日历审核文案。
5. 触发视频生成。
6. 在视频库预览或下载 MP4。

## 后续规划

- 热点选题自动抓取。
- 多博主风格融合。
- 批量视频生成。
- 更真实的数字人口型 / 唇形同步。
- 平台发布 API 对接。
- 更完整的视频质量自动检测。
