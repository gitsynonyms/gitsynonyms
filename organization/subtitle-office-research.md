# 字幕时间精确刻录司：处室职责与研究方案

本规章把字幕时间精确刻录司划分为五个处室。代码能力、已取得的测量证据和待完成的
研究必须分别登记；引用排行榜、静态阈值或脚本测试不能冒充真实视频上的人工评测。

## 处室设置

| 处室 | 职责与交付 | 当前实现与限制 |
|---|---|---|
| 字幕源与时间轴融合处 | 获取可用的七种 YouTube 字幕格式；保存原文件、来源和解析错误；以 JSON3 逐词时间为主，结合其他格式校准，交付 ASS、毫秒 VTT 与时间报告 | `scripts/subtitle_timing.py`；JSON3 原始偏移量为毫秒级，纳秒是无损内部整数单位，不代表纳秒观测精度。ASS/libass 输出为 10 ms 粒度 |
| 第三方平台字幕生成与比较处 | 对同一实际音频运行平台转写，保留 provider 输出，比较词错误率、词级时间偏差、可读性指标、覆盖语言和成本 | `scripts/third_party_captions.py` 已实现 AssemblyAI、Deepgram、Gladia、Rev AI、ElevenLabs、OpenAI API 适配；Otter 等平台登记为 unsupported/skipped，不伪造结果。需要对应 API 凭据才能产生真实服务结果 |
| 语音模型与排行榜研究处 | 至少维护十个公开模型/基准来源；分别记录模型、语言、数据集、WER/CER、延迟/RTFx、成本、许可证、时间和方法，不把不同任务榜单合并为单一“冠军” | `scripts/speech_model_research.py` 目前只刷新 Hugging Face Open ASR 英文短音频 CSV；其他来源须按本页登记，不能说它们已接入自动排名 |
| 字幕人体工学与认知研究处 | 研究阅读速度、视觉辨识、双语行长/字号比例、对比度、持续时间、切换间隔、遮挡和理解效果；提出并验证不同人群预设 | `scripts/subtitle_ergonomics.py` 有静态 cue、字体、对比度和安全区检查；眼动、脑科学/心理学实验及受试者理解度尚未实测 |
| 字幕/信息/Logo 分区与版式研究处 | 标注字幕、演讲者/信息条、台标/Logo、标题安全区、人脸/动作区域；检测冲突并比较无底板、半透明底板和位置调整 | 视频流程可检测字幕带的持续横幅，并按横幅决定总统蓝框或无框；当前不具备可靠的语义式 Logo、人脸与信息条检测/自动避让，需人工/标注评测补齐 |

## 公开模型与基准来源登记

以下十个来源在研究中承担不同角色，不是十个等价的 ASR 准确率排行榜。仅第 1、2 项
目前是可直接用于跨模型 ASR 错误率比较的公开排行；其余用于补充流行度、模型发现、
特定数据集结果或系统吞吐数据。榜单分数只有在语言、数据集、切分、文本规范化、
音频长度、流式模式和计时方法相同后才可横向比较。

| # | 来源 | 可用信号 | 不可据此推断 |
|---:|---|---|---|
| 1 | [Hugging Face Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard) | 英文短音频模型 WER、RTFx 和模型属性；本仓库现有快照/刷新脚本的数据源 | 不代表中文或长视频表现 |
| 2 | [Artificial Analysis Speech-to-Text](https://artificialanalysis.ai/speech-to-text/non-streaming) | 多数据集 ASR WER 及公开的方法说明；适合作为独立交叉参照 | 与 HF 的样本、权重、时长条件不同，原始名次不能直接混排 |
| 3 | [OpenRouter AI Model Rankings](https://openrouter.ai/rankings) | 按 OpenRouter 路由 token 用量衡量模型使用热度；补充发现通用/多模态模型 | 官方说明该榜单不是准确率或 benchmark 排名；不能当 ASR 质量证据 |
| 4 | [Hugging Face ASR 模型目录](https://huggingface.co/models?pipeline_tag=automatic-speech-recognition) | 模型卡、权重、许可证、更新时间和社区下载/点赞信号 | 热度不等于准确率；模型卡指标可能来自不同数据集 |
| 5 | [MLPerf Inference Datacenter](https://mlcommons.org/benchmarks/inference-datacenter/) | 在特定套件/规则下比较系统吞吐、延迟和质量约束 | 系统级结果不是任意 API/ASR 模型的单体精度排名；只纳入该版本明确列出的语音任务 |
| 6 | [ESPnet](https://github.com/espnet/espnet) | 可复现 ASR recipes、数据集和论文结果；可按一致 recipe 重跑 | 不同 recipe 的数字不能未经归一化合并 |
| 7 | [SpeechBrain Benchmarks](https://github.com/speechbrain/benchmarks) | 标准化 benchmark recipes 和任务级对照 | 覆盖的任务/模型取决于仓库版本，不是完整商业服务榜单 |
| 8 | [NVIDIA NeMo ASR results](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/results.html) | 多个预训练检查点及公布的数据集结果 | 厂商工具链结果应标记为厂商来源，不等同于独立第三方盲测 |
| 9 | [OpenAI Whisper repository and paper](https://github.com/openai/whisper) | 模型说明、论文和公开评测的复现入口 | 单一模型系列的结果不能替代多模型同条件实测 |
| 10 | [FLEURS benchmark paper](https://arxiv.org/abs/2205.12446) | 102 种语言的平行语音基准和基线，可补足多语种评估设计 | 数据集论文/基线不是持续更新的在线排行榜 |

### 榜单纳入与排名方法

1. 为每个来源保存检索日期、原始链接、快照/模型版本、任务定义和许可信息。
2. 分榜：英文短音频、中文、跨语言、长音频、流式、离线；不同分榜不交叉宣称总冠军。
3. 质量主指标按适用语种报告 WER；中文同时报告 CER，并记录繁简体、标点、数字和专名
   归一化规则。另报告逐词时间绝对误差中位数/P95、RTFx/端到端延迟、失败率、成本/音频
   小时和许可。
4. 公开排行榜只用于候选筛选。最终候选须用同一段实际素材、相同音轨/声道和统一文本
   规范化，在不改动参考稿的前提下复测；公布样本时长、分段方法和误差范围。
5. 不把 OpenRouter 使用热度、Hugging Face 下载量或单个厂商自测当准确率。不得仅凭
   一个英文榜单推断中文表现。

### 可复核的 Hugging Face 英文短音频快照

本仓库 CSV 快照日期为 2026-10-22。该表只表示其英文短音频评测口径，不等于本项目素材
上的实测结果，也不代表多语种/中文排名：

| 名次 | 模型 | 平均 WER | RTFx | 权重/服务属性 |
|---:|---|---:|---:|---|
| 1 | OtterAI/speech-v3.1 | 3.88% | — | 专有服务 |
| 2 | reson8/resonant-1-flash | 4.02% | — | 专有服务 |
| 3 | zoom/scribe_v1 | 4.15% | — | 专有服务 |
| 4 | microsoft/azure-speech-06-2026 | 4.26% | — | 专有服务 |
| 5 | sophea/asr-k1 (preview) | 4.61% | — | 专有服务 |
| 6 | zoom/scribe_v2_pro | 4.77% | — | 专有服务 |
| 7 | microsoft/Phi-4-multimodal-instruct | 5.02% | 162.5 | MIT |
| 8 | ibm-granite/granite-speech-5.0-2b | 5.06% | 544.5 | Apache-2.0 |
| 9 | microsoft/azure-speech-07-2026 | 5.21% | — | 专有服务 |
| 10 | ibm-granite/granite-speech-3.3-8b | 5.26% | 262.8 | Apache-2.0 |

数字由 `python scripts/speech_model_research.py --top 10 --markdown` 从仓库快照生成；
刷新后榜单可能变化，应同时保存下载时间和原始 CSV。榜单中的 Otter/Zoom 结果仅是榜单
数据，不代表本项目已经通过这些服务 API 转录。

## 第三方平台实测协议

对实际视频链接先取得许可/确认，再由 `scripts/third_party_captions.py` 使用同一视频或
提取的同一音轨、同一语言、同一时间片段运行。参考轨优先使用人工校对稿；YouTube 的
JSON3 融合轨可作为机器生成的时间参照，但不能称为无误差“真值”。

每个 provider 保存状态、请求模型/版本、提交音频 SHA-256、音频时长、耗时、可用词级
时间、原始响应/字幕、语言、失败原因和成本。结果同时报告 WER/CER、对齐词的起始时间
绝对误差中位数/P95、cue CPS/时长/间隔、失败率与费用；指标分列，避免任意权重产生
误导性的综合冠军。API 未配置、供应商无可用 word timestamp、服务失败三种状态分别写为
`skipped`、`unsupported`、`error`。

当前第三方适配器可调用 AssemblyAI、Deepgram、Gladia、Rev AI、ElevenLabs 和 OpenAI；
Otter.ai 已登记，但没有本工具可用的原始逐词时间戳适配器。其他登记平台也不能因为
网页宣传或导出字幕格式存在就被记为已实测。向供应商发送音视频前，应披露服务对象并
遵守其数据保留、隐私和使用条款。

## 字幕人体工学与分区评测

### 现有可机器检查的基线

- **阅读速率**：沿用 `scripts/subtitle_ergonomics.py` 中 Netflix 英文 20/17 CPS、简体
  中文 9/7 CPS（成人/儿童）预设；并报告 BBC/Ofcom 160–180 WPM 建议上限。每条 cue
  同时输出 CPS、字符数、持续时间、间隔和逐项违规，不能只给总分。
- **对比度**：文本相对其实际背景至少 4.5:1 作为常规文本工程基线，依据
  [WCAG 2.2 SC 1.4.3](https://www.w3.org/TR/WCAG22/#contrast-minimum)。视频底图变化时
  必须测量合成后的有效对比度；ASS 颜色表本身不足以证明画面符合。
- **布局**：以 1920×1080 ASS 画布映射到实际分辨率，逐 cue 检查字幕与动作安全区、标题
  安全区、信息/新闻横幅、Logo 角落和人脸区域的重叠。单纯静态区域模板只能发出冲突
  告警，不能声称已识别人脸或 Logo。
- **当前模板基线而非人体工学结论**：中英字号默认约 57.6/48（1.2:1）；总统蓝框占
  1920 画布约 93% 宽、1080 画布约 12.9% 高，默认不透明度 0.85。该模板比例有自动化
  回归测试，但尚无跨设备、跨人群 A/B 结果证明其最优。
- **源画面策略**：已检测到字幕带横幅时用半透明总统蓝底板；可靠判定为干净片源时直接
  显示描边字幕、不添加底板；检测失败保守回退为有底板并记录失败。用户可显式覆盖。

### 需要实测而非静态打分的研究

采用同一素材、相同字幕文案和随机化呈现顺序，比较至少四种条件：干净画面直接字幕、
干净画面半透明框、横幅画面自动避让、横幅画面保守总统蓝框。分别覆盖桌面、手机、亮/
暗背景、含信息条/台标/人脸、单语/双语和正常/儿童阅读预设。

每个版本记录逐帧可读性缺陷、字幕/信息/Logo/人脸相交面积占字幕区面积百分比、合成
对比度、字体像素高度与画面高度比、双语字号/字间距/行间距、每行宽度、CPS、cue
时长、行间空隙、目标词识别率、内容理解正确率、回看/暂停次数和用户偏好。建议每种
画面至少 30 条独立 cue，并进行多设备检查；受试者研究应预先记录样本量、年龄/视力
分层、知情同意和排除标准。

对用户实验可用以下**项目验收门槛**作为首轮假设：核心文本理解率不低于 90%；目标词
识别率不低于 95%；有效对比度符合 4.5:1；被字幕遮挡的信息/Logo/人脸区域面积低于
目标区域 1%；滚动重复 cue 和持续闪烁为零。门槛不是普适医学结论，应由预注册实验
验证后再定版；未收集眼动仪或脑电等数据时，不得声称完成人眼结构、脑科学或心理学
实测。

## 当前完成情况与真实视频阻塞

- 七格式解析、JSON3 优先融合、整数纳秒内部轨迹、ASS 时间量化报告、横幅框自动选择和
  静态人体工学报告已有代码及离线测试。
- White House 发布 workflow 应由视频目录模式调用上述唯一刻录路径；只有实际 Actions
  成功产出 `TIMING.json`、`BURN.json`、ASS、precision VTT、最终 MP4 和帧 QA，才算一次
  生产实测成功。
- 第三方平台工具已有若干 API 适配，但本地缺少这些服务的 API 凭据时无法生成真实供应商
  结果；Otter 目前也没有可执行适配器。排行榜多源登记不代表本仓库已自动同步十个平台。
- 机器检查、公开榜单和离线单元测试不等同于第三方盲测、跨学科人体实验或正式医学结论。
