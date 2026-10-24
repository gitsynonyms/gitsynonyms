# 模块2：视频处理部

## 职责边界

模块2处理**已经由上游选定**的视频和字幕。输入至少要能定位源视频、字幕目录、
目标语言和输出目录；模块2不负责频道扫描、视频选择、资源登记或上游巡检。

## 五个职责单元

| 子部门 | 职责 | 实现 |
|---|---|---|
| 素材接收组 | 读取上游选择清单；校验视频与字幕目录、语言对、文件完整性和输出路径 | `scripts/video_processing.py` |
| 字幕时间精确刻录司 | 统筹字幕源采集、七格式时间融合、第三方平台比较、语音模型研究和字幕人体工学；内部处室与实测边界见 [`subtitle-office-research.md`](subtitle-office-research.md) | `scripts/subtitle_timing.py`、`scripts/third_party_captions.py`、`scripts/speech_model_research.py`、`scripts/subtitle_ergonomics.py` |
| 字幕区判定组 | 检测片源字幕区是否被横幅遮挡；有遮挡时启用半透明总统蓝背景，无横幅的干净片源则直接显示字幕、不画背景框 | `scripts/video_processing.py` |
| 刻录渲染组 | 单遍 libass 硬字幕；按需生成 540p 预览和封面；成功后原子替换最终输出，避免留下半成品 | `scripts/video_processing.py` |
| 质检交接组 | 使用 ffprobe 校验输入/输出；检查字体与缺字；输出 `BURN.json`、`TIMING.json` 及可复核的处理结果 | `scripts/video_processing.py`、`scripts/subtitle_timing.py` |

字幕时间精确刻录司内部设置五个处室，负责研究与可复核评估；处室制度不等同于已经
完成的在线实测或已取得的第三方服务结果。根目录旧档案
[`../MODULE2_ORG.md`](../MODULE2_ORG.md) 仅保留为本页入口，本页是当前模块2职责的
详细说明。

## 时间轴精度规则

1. 读取并融合以下七种 YouTube 格式：`vtt`、`srt`、`ttml`、`srv3`、`srv2`、
   `srv1`、`json3`。JSON3 的逐词 `tOffsetMs` 是主要来源；`srv3`/`srv2` 用作逐词
   时间佐证，其余格式补充 cue 边界。保留可用源格式并记录无法解析的来源。
2. 融合时间轴以整数纳秒表示；YouTube 源时间分辨率最高为 1 毫秒，因此纳秒是无损
   的内部计量单位，**不代表源数据含纳秒级观测精度**。不从量化值虚构更细的来源精度。
3. `TIMING.json` 保存纳秒整数时间轴、来源与融合统计。`*.precision.vtt` 以毫秒写出；
   ASS/libass 只接受厘秒时间戳，刻录前按 10 毫秒量化，最大量化误差 5 毫秒，并将误差
   写入时间报告/ASS 信息。因而“优于毫秒”仅适用于内部时间轴表示，不适用于 ASS
   刻录结果；纳秒也不是 YouTube 原始字幕精度。量化后的每个有效 ASS 事件必须仍有正时长；
   对短于 10 毫秒的派生事件，至少保留一个厘秒且不得越过既定视频边界容差。
4. 原始下载字幕文件保持字节不变；合并字幕及七格式融合后的 ASS/precision VTT 仅对派生
   时间轴裁剪：cue 与逐词时间不得超出视频时长。`TIMING.json` 记录源 cue 数、裁剪/丢弃
   数及源字幕末 cue 时间，确保修正片尾越界时仍可复核原始数据。

## 字幕区与画面规则

- **检测到横幅遮挡字幕区**：选择不覆盖关键信息的位置，绘制半透明总统蓝
  `#0F1647` 背景框，再显示字幕；透明度须可配置并写入 `BURN.json`。
- **确认是干净片源**：不绘制背景框或遮挡区，只显示字幕。
- **自动检测失败**：采用总统蓝框的保守回退，避免横幅遮挡导致字幕不可读，并在
  `BURN.json` 记录错误和回退来源；不得静默按“干净”处理。用户可显式强制开/关。
- `BURN.json` 应记录检测结论、置信信息、区域坐标、所用样式及渲染参数。
- 自动字幕区判定适用于从七格式字幕目录生成 ASS 的流程；若上游交付的是已排版 ASS，
  当前实现保留其内嵌布局，不会自动重排或补加字幕框。

## 实现与运行边界

- `video_processing.py` 接收已选择的视频/字幕清单，运行 ffprobe 检查，自动判定横幅，
  单遍 libass 刻录，并按选项生成 540p 预览、封面和 `BURN.json`；刻录到临时文件，
  检查输出后通过原子替换发布最终 MP4。
- `subtitle_timing.py` 发现七种格式、融合 JSON3 优先的逐词时间，生成 ASS、
  `TIMING.json` 和毫秒级 precision VTT。ASS 输出的 10 毫秒粒度与 5 毫秒最大量化误差
  是格式/渲染器边界，不得描述为亚毫秒精度。
- `subtitle_ergonomics.py` 检查字幕人体工学约束，并由视频处理流程汇总告警/结果。
- White House 主 workflow 将下载的字幕原始格式交给 `video_processing.py`；七格式融合、
  纳秒内部时间轴、精确 ASS、`TIMING.json` 和自动字幕框判定由该流程生成。ASS/libass
  的最终刻录粒度仍为 10 毫秒。
- `third_party_captions.py` 与 `speech_model_research.py` 是可选研究工具，不是刻录主
  链路的必要依赖；需要 API 凭据的厂商实测不可用代码存在性代替。
- 独立模块本身已有实现和离线测试；是否接入具体的上游自动任务，应以对应 workflow
  调用和运行证据为准，不因模块存在就宣称云端端到端自动发布已完成。

## 交接验收

处理任务完成时，至少应交付最终 MP4、ASS、`BURN.json`、`TIMING.json` 和质检结果；
使用 caption-directory 模式时，还应交付毫秒级 `*.precision.vtt`。失败时保留明确错误
与失败阶段，不将部分视频作为成功输出；最终文件应在校验通过后原子替换。
