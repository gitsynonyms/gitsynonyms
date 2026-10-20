const REPOSITORY = "gitsynonyms/gitsynonyms";
const WORKFLOW_URL = `https://github.com/${REPOSITORY}/actions/workflows/youtube-subtitles.yml`;
const SCRIPT_URL = `https://raw.githubusercontent.com/${REPOSITORY}/main/scripts/download_youtube_bilingual_srt.py`;

const form = document.querySelector("#download-form");
const urlInput = document.querySelector("#video-url");
const runLocation = document.querySelector("#run-location");
const burnInInput = document.querySelector("#burn-in");
const burnInLabel = document.querySelector("#burn-in-label span");
const instructions = document.querySelector("#run-instructions");
const instructionsTitle = document.querySelector("#instructions-title");
const instructionsCopy = document.querySelector("#instructions-copy");
const commandOutput = document.querySelector("#run-command");
const copyButton = document.querySelector("#copy-command");
const workflowLink = document.querySelector("#workflow-link");
const statusRegion = document.querySelector("#status");
const settingsSummary = document.querySelector("#caption-settings-summary");

function quotePowerShell(value) {
  return `'${value.replaceAll("'", "''")}'`;
}

function readCaptionSettings() {
  try {
    return window.CaptionSettings.read();
  } catch (error) {
    statusRegion.hidden = false;
    statusRegion.classList.add("error");
    statusRegion.textContent = `读取字幕设置失败：${error.message}`;
    throw error;
  }
}

function renderCaptionSettingsSummary() {
  try {
    const settings = readCaptionSettings();
    const style = settings.stylePreset === "cinema" ? "电影风格 · 英文在上" : "经典风格 · 中文在上";
    const background = settings.boxStyle === "presidential-blue"
      ? `总统蓝 ${settings.boxColor.toUpperCase()}`
      : settings.boxStyle === "solid-color"
        ? `纯色 ${settings.boxColor.toUpperCase()}`
        : "磨砂玻璃 · 钻石切面";
    const display = settings.boxDisplay === "always" ? "背景全程显示" : "背景随字幕显示";
    settingsSummary.textContent = `${style} · ${background} · ${display}`;
  } catch {
    settingsSummary.textContent = "无法读取字幕设置，请前往设置页检查浏览器本地存储。";
  }
}

function makeCommand(videoUrl, location, options) {
  const quotedUrl = quotePowerShell(videoUrl);
  if (location === "local") {
    const burnArgs = options.burnIn
      ? " --burn-in --video-encoder h264_qsv --global-quality 1"
      : "";
    return [
      `$script = Join-Path $PWD 'download_youtube_bilingual_srt.py'`,
      `Invoke-WebRequest -Uri '${SCRIPT_URL}' -OutFile $script`,
      `& (Join-Path $env:USERPROFILE 'anaconda3\\python.exe') $script ${quotedUrl} --languages zh-Hans,en-orig --output-dir 'D:\\MyDownload' --style-preset ${options.stylePreset} --box-display ${options.boxDisplay} --box-style ${options.boxStyle} --chinese-font ${quotePowerShell(options.chineseFont)} --english-font ${quotePowerShell(options.englishFont)} --english-font-size ${options.englishFontSize} --chinese-font-scale ${options.chineseFontScale} --chinese-color ${quotePowerShell(options.chineseColor)} --english-color ${quotePowerShell(options.englishColor)} --box-color ${quotePowerShell(options.boxColor)} --box-opacity ${options.boxOpacity} --box-x ${options.boxX} --box-y ${options.boxY} --box-width ${options.boxWidth} --box-height ${options.boxHeight}${burnArgs}`,
    ].join("; ");
  }
  const workflowInputs = [
    ["source", quotedUrl],
    ["burn_in", String(options.burnIn)],
    ["style_preset", options.stylePreset],
    ["box_display", options.boxDisplay],
    ["box_style", options.boxStyle],
    ["chinese_font", quotePowerShell(options.chineseFont)],
    ["english_font", quotePowerShell(options.englishFont)],
    ["english_font_size", String(options.englishFontSize)],
    ["chinese_font_scale", String(options.chineseFontScale)],
    ["chinese_color", quotePowerShell(options.chineseColor)],
    ["english_color", quotePowerShell(options.englishColor)],
    ["box_color", quotePowerShell(options.boxColor)],
    ["box_opacity", String(options.boxOpacity)],
    ["box_x", String(options.boxX)],
    ["box_y", String(options.boxY)],
    ["box_width", String(options.boxWidth)],
    ["box_height", String(options.boxHeight)],
  ].map(([name, value]) => `-f ${name}=${value}`).join(" ");
  return `gh workflow run youtube-subtitles.yml --repo ${REPOSITORY} --ref main ${workflowInputs}`;
}

function syncBurnInRunnerRequirement() {
  burnInLabel.textContent =
    runLocation.value === "local"
      ? "生成原视频和带硬字幕 MP4（本机需要 Intel Quick Sync 与支持 h264_qsv 的 FFmpeg）"
      : "生成原视频和带硬字幕 MP4（云端使用软件编码）";
}

burnInInput.addEventListener("change", syncBurnInRunnerRequirement);
runLocation.addEventListener("change", syncBurnInRunnerRequirement);
syncBurnInRunnerRequirement();
renderCaptionSettingsSummary();
window.addEventListener("storage", (event) => {
  if (event.key === window.CaptionSettings.storageKey) renderCaptionSettingsSummary();
});
window.addEventListener("pageshow", renderCaptionSettingsSummary);

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const videoUrl = urlInput.value.trim();
  const location = runLocation.value;
  if (!videoUrl) return;
  let settings;
  try {
    settings = readCaptionSettings();
  } catch {
    return;
  }
  const displayDescription = settings.boxDisplay === "always"
    ? "字幕背景框会从视频开始持续到结束，适合遮挡原片横幅"
    : "字幕背景框只在字幕出现时显示";
  const boxStyleDescription = settings.boxStyle === "presidential-blue"
    ? "总统蓝背景框"
    : settings.boxStyle === "solid-color"
      ? "纯色自定义背景框"
    : "磨砂玻璃钻石质感背景框";
  const options = {
    burnIn: burnInInput.checked,
    ...settings,
  };

  instructions.hidden = false;
  statusRegion.hidden = true;
  commandOutput.textContent = makeCommand(videoUrl, location, options);
  workflowLink.hidden = location !== "runner";
  workflowLink.href = WORKFLOW_URL;

  if (location === "local") {
    instructionsTitle.textContent = "本地 Anaconda 运行";
    instructionsCopy.textContent = options.burnIn
      ? `点击复制后，在 Windows PowerShell 中粘贴运行；命令通过 $env:USERPROFILE 定位 Anaconda。本机会下载视频和原始字幕，生成${boxStyleDescription}、${displayDescription}的双语 ASS，再以 h264_qsv / -global_quality 1 硬刻录。需要 yt-dlp、FFmpeg（含 h264_qsv）和 Intel Quick Sync。`
      : `点击复制后，在 Windows PowerShell 中粘贴运行；会生成原始单语字幕、双语 SRT 和${boxStyleDescription}，${displayDescription}的 ASS。`;
  } else {
    instructionsTitle.textContent = "GitHub 云端 runner 运行";
    instructionsCopy.textContent =
      options.burnIn
        ? `复制命令后，在已登录且有仓库 Actions 写权限的终端执行；云端会下载字幕和视频，生成${boxStyleDescription}、${displayDescription}的双语 SRT/ASS 并硬刻录 MP4，到 Actions artifact 下载。`
        : `复制命令后，在已登录且有仓库 Actions 写权限的终端执行；云端会生成单语字幕、双语 SRT 和${boxStyleDescription}、${displayDescription}的 ASS，到 Actions artifact 下载。也可打开 Actions 页面手动启动 workflow。`;
  }
});

copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(commandOutput.textContent);
    copyButton.textContent = "已复制";
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(commandOutput);
    selection.removeAllRanges();
    selection.addRange(range);
    copyButton.textContent = "已选中，请复制";
  }
  window.setTimeout(() => {
    copyButton.textContent = "复制命令";
  }, 2000);
});
