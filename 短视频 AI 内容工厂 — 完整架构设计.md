短视频 AI 内容工厂 — 完整架构设计                                                                                                           

 Context

 构建一个 AI 驱动的短视频内容生产系统。核心流程：用户给定一个对标博主链接 → 系统下载视频、提取音频、语音转文字 → Claude
 蒸馏博主的内容基因（价值观定位、爆款技巧、选题偏好、话术风格） → 每天自动基于该基因生成原创文案 → TTS 合成语音 → 用户照片 + 语音 +
 字幕自动合成 MP4 视频 → 可发布至抖音/小红书/快手。

 用户角色：短视频业务工作者，不需要自己出镜拍摄，系统全自动产出视频内容。

 ---
 一、系统架构总览

 ┌──────────────────────────────────────────────────────┐
 │                    前端 (React)                        │
 │  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐ │
 │  │ 博主管理  │ │ 内容日历  │ │ 视频库  │ │ 系统设置   │ │
 │  └──────────┘ └──────────┘ └────────┘ └───────────┘ │
 └──────────────────────┬───────────────────────────────┘
                        │ REST API + WebSocket
 ┌──────────────────────┴───────────────────────────────┐
 │                  后端 (Python FastAPI)                  │
 │                                                       │
 │  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐ │
 │  │ 博主蒸馏模块│ │ 文案生成模块│ │ TTS模块 │ │ 视频合成模块│ │
 │  └─────┬─────┘ └─────┬─────┘ └───┬────┘ └─────┬─────┘ │
 │        │              │           │             │       │
 │  ┌─────┴─────┐  ┌─────┴─────┐ ┌──┴──┐  ┌──────┴─────┐ │
 │  │yt-dlp下载  │  │ Claude API│ │TTS引擎│ │  FFmpeg   │ │
 │  │Whisper转写 │  │ 文案生成   │ │      │ │  视频合成  │ │
 │  │Claude蒸馏  │  │           │ │      │ │  字幕渲染  │ │
 │  └───────────┘  └───────────┘ └─────┘  └────────────┘ │
 │                                                       │
 │  ┌──────────────────────────────────────────────────┐ │
 │  │              任务队列 (Celery + Redis)              │ │
 │  │   异步处理: 视频下载、Whisper转写、视频合成等耗时任务  │ │
 │  └──────────────────────────────────────────────────┘ │
 │                                                       │
 │  ┌──────────────────────────────────────────────────┐ │
 │  │                   SQLite 数据库                     │ │
 │  └──────────────────────────────────────────────────┘ │
 └───────────────────────────────────────────────────────┘

 ---
 二、技术栈

 ┌──────────────┬────────────────────────────┬───────────────────────────────┐
 │      层      │            选型            │             说明              │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ 前端         │ React + Vite + TailwindCSS │ 轻量现代前端                  │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ 后端         │ Python 3.11+ FastAPI       │ 异步支持好，生态丰富          │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ 任务队列     │ Celery + Redis             │ 处理视频下载、转写等耗时任务  │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ 数据库       │ SQLite + SQLAlchemy        │ 单机够用，零配置              │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ AI 分析      │ Claude API (Sonnet)        │ 蒸馏分析 + 文案生成           │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ 语音转文字   │ OpenAI Whisper (本地)      │ 中文识别效果好，免费          │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ 视频下载     │ yt-dlp                     │ 多平台支持                    │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ TTS 语音合成 │ Edge-TTS                   │ 免费，中文多音色              │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ 视频合成     │ FFmpeg                     │ 音频+图片+字幕合成            │
 ├──────────────┼────────────────────────────┼───────────────────────────────┤
 │ 场景化背景   │ Playwright                 │ 模拟浏览器访问视频页面 + 截图 │
 └──────────────┴────────────────────────────┴───────────────────────────────┘

 ---
 三、核心数据模型

 3.1 表结构

 bloggers (博主表)
 ├── id: UUID
 ├── name: str              # 博主名称
 ├── platform: str          # 平台: douyin/xiaohongshu/kuaishou
 ├── profile_url: str       # 博主主页链接
 ├── profile_image: str     # 头像/照片 path
 ├── status: str            # pending/analyzing/ready/error
 ├── created_at: datetime
 └── updated_at: datetime

 blogger_content_dna (博主内容基因 - 蒸馏结果)
 ├── id: UUID
 ├── blogger_id: FK -> bloggers
 ├── value_positioning: JSON    # 价值观定位 {core_values, persona, target_audience}
 ├── viral_techniques: JSON     # 爆款技巧 {hook_patterns, narrative_structures, rhythm}
 ├── content_preferences: JSON  # 选题偏好 {topics, angles, taboo_topics}
 ├── language_style: JSON       # 话术风格 {tone, catchphrases, sentence_patterns}
 ├── content_calendar: JSON     # 发布节奏 {frequency, best_times, formats}
 ├── version: int               # 版本号，支持重新分析
 ├── created_at: datetime

 source_videos (源视频 - 下载的博主视频)
 ├── id: UUID
 ├── blogger_id: FK -> bloggers
 ├── source_url: str           # 原始视频链接
 ├── video_path: str           # 下载后本地路径
 ├── audio_path: str           # 提取的音频路径
 ├── transcript: text          # Whisper 转写全文
 ├── metadata: JSON            # 标题、点赞、评论等
 ├── status: str               # downloading/transcribing/analyzed/error
 ├── created_at: datetime

 generated_scripts (生成的文案)
 ├── id: UUID
 ├── blogger_id: FK -> bloggers
 ├── title: str                # 选题标题
 ├── script: text              # 完整口播文案
 ├── hook: str                 # 开头钩子
 ├── hashtags: JSON            # 标签建议
 ├── visual_suggestion: str    # 画面建议
 ├── status: str               # pending/approved/rejected/generated_video
 ├── scheduled_date: date      # 计划发布日期
 ├── created_at: datetime

 generated_videos (生成的视频)
 ├── id: UUID
 ├── script_id: FK -> generated_scripts
 ├── video_path: str           # 最终 MP4 路径
 ├── audio_path: str           # TTS 音频路径
 ├── subtitle_path: str        # 字幕文件路径
 ├── duration: int             # 时长(秒)
 ├── status: str               # generating/ready/error
 ├── created_at: datetime

 user_profile (用户资料配置)
 ├── id: UUID
 ├── photo_path: str           # 用户照片路径
 ├── tts_voice: str            # 选择的 TTS 音色
 ├── watermark: str            # 水印/账号名
 └── video_style: JSON         # 视频风格配置(分辨率/背景/字幕样式)

 ---
 四、API 设计

 4.1 博主管理

 POST   /api/bloggers                    # 添加博主（输入链接）
 GET    /api/bloggers                    # 博主列表
 GET    /api/bloggers/{id}               # 博主详情 + 内容基因
 DELETE /api/bloggers/{id}               # 删除博主
 POST   /api/bloggers/{id}/analyze       # 触发蒸馏分析
 POST   /api/bloggers/{id}/re-analyze    # 重新蒸馏（新增视频后）
 GET    /api/bloggers/{id}/videos        # 博主的源视频列表

 4.2 源视频管理

 POST   /api/bloggers/{id}/videos        # 添加视频（粘贴链接）
 GET    /api/videos/{id}                 # 视频详情 + 转写状态
 POST   /api/videos/{id}/transcribe      # 触发语音转文字
 DELETE /api/videos/{id}                 # 删除视频

 4.3 文案生成

 POST   /api/scripts/generate            # 手动触发生成（指定博主ID）
 GET    /api/scripts                     # 文案列表（支持按日期/博主筛选）
 GET    /api/scripts/{id}                # 单条文案详情
 PATCH  /api/scripts/{id}                # 编辑文案
 DELETE /api/scripts/{id}                # 删除文案
 POST   /api/scripts/{id}/approve        # 审批通过
 POST   /api/scripts/{id}/reject         # 驳回

 4.4 视频生成

 POST   /api/scripts/{id}/generate-video # 基于文案生成视频
 GET    /api/videos-generated            # 已生成视频列表
 GET    /api/videos-generated/{id}       # 视频详情
 DELETE /api/videos-generated/{id}       # 删除视频
 GET    /api/videos-generated/{id}/download  # 下载视频文件

 4.5 系统配置

 GET    /api/settings                    # 获取配置
 PUT    /api/settings                    # 更新配置（照片/TTS音色等）
 GET    /api/settings/tts-voices         # 获取可用 TTS 音色列表

 4.6 定时任务

 GET    /api/scheduler/status            # 调度器状态
 POST   /api/scheduler/toggle            # 开关每日自动生成

 ---
 五、核心处理管道

 5.1 博主蒸馏管道 (Blogger Distill Pipeline)

 用户粘贴视频链接
       │
       ▼
 ┌─────────────────┐
 │ 1. 视频下载       │  yt-dlp 下载视频 → 存本地 storage/videos/{blogger_id}/
 │   (yt-dlp)       │  失败 → playwright 录屏 → 失败 → 提示手动上传
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 2. 音频提取       │  FFmpeg: 从 MP4 提取 AAC/MP3 音频
 │   (FFmpeg)       │
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 3. 语音转文字     │  Whisper: audio → 中文转录文本 (带时间戳)
 │   (Whisper)      │
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 4. AI 蒸馏分析    │  Claude API: 输入转录文本 + 标题标签
 │   (Claude API)   │  输出: 内容基因 JSON
 │                  │  - 价值观定位 (人设、核心理念、目标受众)
 │                  │  - 爆款技巧 (钩子模式、叙事结构、情绪节奏)
 │                  │  - 选题偏好 (常做话题、切入角度、避开话题)
 │                  │  - 话术风格 (语气、金句、句式特点)
 │                  │  - 发布策略 (频率、最佳时段、内容形式)
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 5. 存储内容基因   │  写入 blogger_content_dna 表
 │    + 更新状态     │  博主状态 → ready
 └─────────────────┘

 5.2 每日文案生成管道 (Script Generation Pipeline)

 定时触发 (每天 8:00) / 手动触发
       │
       ▼
 ┌─────────────────┐
 │ 1. 加载内容基因   │  从 DB 读取 blogger_content_dna
 │    + 历史文案     │  加载最近 7 天已生成的文案（避免重复）
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 2. AI 生成文案   │  Claude API:
 │   (Claude API)  │  输入: 内容基因 + 历史文案 + 今日热点(可选) + 用户照片风格
 │                  │  输出: 3-5 条选题 × 完整口播文案
 │                  │  每条包含:
 │                  │  - 标题/选题
 │                  │  - 钩子 (前3秒抓人)
 │                  │  - 正文口播
 │                  │  - 结尾引导 (关注/评论/点赞)
 │                  │  - 标签建议
 │                  │  - 画面/镜头建议
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 3. 写入数据库     │  存储到 generated_scripts，状态 pending
 │   (SQLite)       │  等待用户在 Dashboard 审核
 └─────────────────┘

 5.3 视频合成管道 (Video Synthesis Pipeline)

 用户审批通过 → 触发生成视频
       │
       ▼
 ┌─────────────────┐
 │ 1. TTS 语音合成  │  Edge-TTS: 文案文本 → MP3 音频
 │   (Edge-TTS)    │  可选: 火山引擎 TTS / Azure TTS 提高质量
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 2. 字幕生成      │  基于 TTS 时间戳 + 文本，生成 SRT/ASS 字幕
 │                  │  支持竖屏字幕样式（底部大字 + 关键词高亮）
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 3. 视频合成      │  FFmpeg:
 │   (FFmpeg)      │  背景(用户照片+Ken Burns慢速缩放)
 │                  │  + TTS 音频
 │                  │  + 字幕叠加
 │                  │  + 可选背景音乐
 │                  │  → 输出: 1080×1920 (竖屏) MP4
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ 4. 存储 + 通知    │  写入 generated_videos，状态 ready
 │                  │  Dashboard 可预览/下载
 └─────────────────┘

 ---
 六、前端页面设计

 应用布局
 ├── 侧边栏导航
 │   ├── 📊 仪表盘 (Dashboard)
 │   ├── 👤 博主管理 (Bloggers)
 │   ├── 📝 内容日历 (Content Calendar)
 │   ├── 🎬 视频产出 (Video Library)
 │   └── ⚙️  系统设置 (Settings)
 │
 ├── 仪表盘
 │   ├── 统计卡片 (博主数/本周文案/本月视频)
 │   ├── 今日待审核文案列表
 │   ├── 最近生成的视频预览
 │   └── 调度器状态
 │
 ├── 博主管理
 │   ├── 博主列表 (头像/平台/状态/视频数)
 │   ├── 添加博主对话框 (粘贴链接)
 │   ├── 博主详情页
 │   │   ├── 内容基因可视化 (价值观/爆款技巧/话术风格/选题偏好)
 │   │   ├── 源视频列表 (转写状态/转录文本预览)
 │   │   ├── 添加视频按钮
 │   │   └── 重新蒸馏按钮
 │   └── 操作(删除/重新分析)
 │
 ├── 内容日历
 │   ├── 日历视图 / 列表视图
 │   ├── 按日期显示已生成文案
 │   ├── 每条文案: 预览/编辑/审批/驳回/生成视频
 │   ├── 手动生成按钮 (选择博主→生成今日文案)
 │   └── 批量审批
 │
 ├── 视频库
 │   ├── 已生成视频网格
 │   ├── 视频预览播放
 │   ├── 下载按钮
 │   └── 按博主/日期筛选
 │
 └── 系统设置
     ├── 上传我的照片
     ├── 选择 TTS 音色 (试听)
     ├── 视频配置 (分辨率/字幕样式/背景风格)
     └── 定时生成配置 (每日生成时间/生成数量)

 ---
 七、项目目录结构

 distill-skill/
 ├── backend/
 │   ├── main.py                    # FastAPI 入口
 │   ├── config.py                  # 配置管理
 │   ├── database.py                # SQLAlchemy 连接
 │   ├── models/
 │   │   ├── blogger.py
 │   │   ├── source_video.py
 │   │   ├── generated_script.py
 │   │   ├── generated_video.py
 │   │   └── user_profile.py
 │   ├── routers/
 │   │   ├── bloggers.py
 │   │   ├── videos.py
 │   │   ├── scripts.py
 │   │   ├── generated_videos.py
 │   │   └── settings.py
 │   ├── services/
 │   │   ├── distill_service.py      # 蒸馏分析核心逻辑
 │   │   ├── script_generator.py     # 文案生成
 │   │   ├── tts_service.py          # TTS 语音合成
 │   │   ├── video_composer.py       # FFmpeg 视频合成
 │   │   ├── video_downloader.py     # yt-dlp / playwright 下载
 │   │   └── whisper_service.py      # Whisper 语音转文字
 │   ├── tasks/
 │   │   ├── celery_app.py           # Celery 配置
 │   │   ├── distill_tasks.py        # 蒸馏异步任务
 │   │   └── video_tasks.py          # 视频合成异步任务
 │   ├── scheduler.py                # 每日定时任务
 │   └── storage/                    # 本地文件存储
 │       ├── videos/                 # 源视频
 │       ├── audio/                  # 音频文件
 │       ├── transcripts/            # 转写文本
 │       ├── generated_audio/        # TTS 音频
 │       ├── generated_videos/       # 最终视频
 │       └── user/                   # 用户照片
 ├── frontend/
 │   ├── src/
 │   │   ├── App.jsx
 │   │   ├── pages/
 │   │   │   ├── Dashboard.jsx
 │   │   │   ├── BloggerList.jsx
 │   │   │   ├── BloggerDetail.jsx
 │   │   │   ├── ContentCalendar.jsx
 │   │   │   ├── VideoLibrary.jsx
 │   │   │   └── Settings.jsx
 │   │   ├── components/
 │   │   │   ├── Layout.jsx
 │   │   │   ├── BloggerCard.jsx
 │   │   │   ├── ScriptCard.jsx
 │   │   │   ├── VideoPlayer.jsx
 │   │   │   ├── DnaVisualizer.jsx
 │   │   │   └── TTSVoiceSelector.jsx
 │   │   └── api/
 │   │       └── client.js
 │   ├── index.html
 │   ├── package.json
 │   └── vite.config.js
 ├── requirements.txt
 ├── docker-compose.yml              # Redis + 应用
 └── README.md

 ---
 八、实现计划（分阶段）

 Phase 1：后端骨架 + 博主蒸馏核心链路

 目标：跑通"粘贴链接 → 下载视频 → 音频提取 → Whisper 转写 → Claude 蒸馏 → 存储"

 - FastAPI 项目初始化，SQLAlchemy 建表
 - video_downloader.py：yt-dlp 下载 + playwright 录制降级
 - whisper_service.py：本地 Whisper 模型加载 + 转写
 - distill_service.py：Claude API 蒸馏分析 Prompt 设计
 - /api/bloggers CRUD + analyze 接口
 - Celery 任务队列配置

 验证：curl 添加一个博主链接，等待分析完成，DB 中有 blogger_content_dna 数据

 Phase 2：文案生成 + TTS 语音合成

 目标：跑通"基于内容基因 → 每日生成原创文案 → TTS 合成语音"

 - script_generator.py：Claude API 文案生成 Prompt 设计
 - tts_service.py：Edge-TTS 集成
 - 每日定时任务 scheduler（Celery Beat）
 - /api/scripts 全套接口
 - 文案去重逻辑（对比历史文案避免重复）

 验证：手动触发文案生成，拿到口播文案 + TTS 音频文件可播放

 Phase 3：视频合成

 目标：跑通"用户照片 + TTS 音频 + 字幕 → MP4 视频"

 - video_composer.py：FFmpeg 合成逻辑
   - 照片 + Ken Burns 慢速缩放效果
   - 字幕 SRT 生成 + 渲染
   - 竖屏 1080×1920 输出
 - /api/videos-generated 接口
 - 视频状态回调

 验证：触发生成视频，拿到可播放的 MP4

 Phase 4：前端 Dashboard

 目标：完整的 Web 交互界面

 - React + Vite + TailwindCSS 初始化
 - 博主管理页（添加/列表/详情/内容基因可视化）
 - 内容日历页（文案审核/编辑/生成视频）
 - 视频库页（预览/下载）
 - 系统设置页（照片上传/音色选择）

 验证：完整链路 UI 操作，从添加博主到下载视频

 Phase 5：增强功能（可选后续）

 - 内容基因可视化对比（多博主对比）
 - 热点话题自动抓取 + 融入文案
 - 多博主混合风格生成
 - 批量视频生成
 - 发布平台 API 对接（自动发布）

 ---
 九、关键设计决策

 9.1 Claude API Prompt 设计要点

 蒸馏分析 Prompt（输入多条视频转录文本）:
 你是一位短视频内容策略专家。请分析以下博主的内容，提取：
 1. 价值观定位：核心理念、人设、目标受众画像
 2. 爆款技巧：开头钩子模式、叙事结构、情绪节奏、互动引导
 3. 选题偏好：高频话题、切入角度、禁忌话题
 4. 话术风格：语气、口头禅、句式长度、节奏感
 5. 发布策略：频率、时长、内容形式

 请以 JSON 格式输出。

 文案生成 Prompt（输入内容基因 + 历史文案）:
 基于以下博主内容基因，生成 5 条原创短视频文案：
 - 保持价值观定位一致，但选题和具体内容全新
 - 使用相同的爆款技巧框架
 - 模仿话术风格，但不能照搬原话
 - 每条包含：钩子(3秒)、正文、结尾引导、标签建议

 9.2 视频下载降级策略

 优先级1: yt-dlp 直接下载 (最省资源)
 优先级2: Playwright 模拟浏览器录屏 (绕过简单反爬)
 优先级3: 提示用户手动上传视频文件 (最终兜底)
 优先级4: 提示用户粘贴文案文本 (跳过视频分析，直接分析文案)

 9.3 TTS 音色选择

 - MVP 用 Edge-TTS（免费，中文音色丰富）
 - 后续可升级：火山引擎 TTS（抖音官方）、Azure TTS（微软）
 - 声音克隆需额外服务

 ---
 十、验证方式

 1. 单元测试：pytest 测试各 service 核心逻辑
 2. 集成测试：启动 FastAPI → 添加博主 → 等待分析完成 → 检查 DB 数据
 3. 端到端测试：前端操作 → 添加博主链接 → 查看蒸馏结果 → 生成文案 → 预览音频 → 生成视频 → 下载 MP4 播放
 4. 视频质量：检查生成的 MP4 分辨率、字幕准确性、音频同步