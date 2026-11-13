# 写实虚构面试制作

`scripts/render_realistic_interview.py` 接收已审核的中文对话时间线、完整配音和两位角色定妆图，使用本地 SadTalker 合成逐句口型，再制作 1080×1920、25 fps 的完整视频。画面始终标明“AI声像模拟·虚构面试 · 非真实招聘”。人物、台词及筛选规则不能冒充真实采访或招聘记录。

模型仓库、权重、人物素材、参考录音与输出视频只保存在忽略目录；不能提交到 Git。语音准备与人物图生成独立于本脚本，不能把已有静态图片或完整配音标记成已完成动态视频。

## 输入

工作目录应有 `写实版对话时间线.json`、`写实版完整对话配音.wav`、`老黄正反打特写.png`、`HR正反打特写.png`、`写实角色定妆图.png`。时间线包括 `duration`、`fiction_notice` 与 `dialogue`；每句有 `speaker`（huang/hr/narrator）、`text`、`headline`、`start`、`end`、`cut_end`，并须连续覆盖配音。单句角色片段最长 12 秒，超过时先拆句。

## 本机环境

在 `backend/engines/SadTalker` 单独安装官方项目与 Python 3.10 虚拟环境，按上游模型说明准备 512 safetensors、full 模式 mapping 和人脸检测、对齐权重。遵守上游代码及各权重许可。环境与工厂主虚拟环境隔离；本次使用 torch 2.5.1/cu124、torchvision 0.20.1、numpy 1.23.4、scipy 1.10.1，适配现有 CUDA 驱动。

脚本还调用工厂的内存保护，需要 `pydantic-settings`。旧 BasicSR 如果导入已移除的 `torchvision.transforms.functional_tensor`，应在该隔离环境改用 `torchvision.transforms.functional.rgb_to_grayscale`，不要修改其他语音引擎环境或系统驱动。

先用短音频验证检测、人脸形变、嘴型与声音；确认结果后再跑完整对话。两位角色可以分配两张 GPU，分别运行：

```bash
CUDA_VISIBLE_DEVICES=0 backend/engines/SadTalker/.venv/bin/python scripts/render_realistic_interview.py WORK_DIR --actor huang
CUDA_VISIBLE_DEVICES=1 backend/engines/SadTalker/.venv/bin/python scripts/render_realistic_interview.py WORK_DIR --actor hr
```

两位角色全部完成后，在工厂主环境合成：

```bash
.venv/bin/python scripts/render_realistic_interview.py WORK_DIR --compose
```

完成的逐句文件可复用以恢复中断任务；不把任何已完成句片段重复循环填充其他句。改动配音、角色图或时间线时使用新工作目录，避免误用旧缓存。不要让两个进程同时制作同一角色目录。

## 验收与交付

核对成片完整解码、配音时长、逐句字幕、首帧中上标题和持续虚构标识，再生成中文名称的平台 ZIP、标题、正文、AI说明及互动文案。生成成功不等于平台提交，提交回执也不等于公开发布。恢复旧版时保留旧版本，不覆盖历史素材与平台证据。

后台服务属于持续任务；本次用户要求“做完停止”时，完成本轮交付后关闭后续生产和发布开关、停止生产与账号连接后台，保留查看和下载服务。
