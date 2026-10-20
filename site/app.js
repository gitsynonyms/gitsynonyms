const REPOSITORY = "gitsynonyms/gitsynonyms";
const WORKFLOW_URL = `https://github.com/${REPOSITORY}/actions/workflows/youtube-subtitles.yml`;
const SCRIPT_URL = `https://raw.githubusercontent.com/${REPOSITORY}/main/scripts/download_youtube_bilingual_srt.py`;

const form = document.querySelector("#download-form");
const urlInput = document.querySelector("#video-url");
const runLocation = document.querySelector("#run-location");
const burnInInput = document.querySelector("#burn-in");
const burnInLabel = document.querySelector("#burn-in-label span");
const chineseFontInput = document.querySelector("#chinese-font");
const englishFontInput = document.querySelector("#english-font");
const englishFontSizeInput = document.querySelector("#english-font-size");
const chineseFontScaleInput = document.querySelector("#chinese-font-scale");
const chineseColorInput = document.querySelector("#chinese-color");
const englishColorInput = document.querySelector("#english-color");
const boxColorInput = document.querySelector("#box-color");
const boxColorHexInput = document.querySelector("#box-color-hex");
const boxOpacityInput = document.querySelector("#box-opacity");
const boxXInput = document.querySelector("#box-x");
const boxYInput = document.querySelector("#box-y");
const boxWidthInput = document.querySelector("#box-width");
const boxHeightInput = document.querySelector("#box-height");
const styleInputs = [...document.querySelectorAll('input[name="style-preset"]')];
const boxDisplayInputs = [...document.querySelectorAll('input[name="box-display"]')];
const boxStyleInputs = [...document.querySelectorAll('input[name="box-style"]')];
const instructions = document.querySelector("#run-instructions");
const instructionsTitle = document.querySelector("#instructions-title");
const instructionsCopy = document.querySelector("#instructions-copy");
const commandOutput = document.querySelector("#run-command");
const copyButton = document.querySelector("#copy-command");
const workflowLink = document.querySelector("#workflow-link");
const statusRegion = document.querySelector("#status");

function quotePowerShell(value) {
  return `'${value.replaceAll("'", "''")}'`;
}

const stylePresets = {
  classic: {
    chineseFont: "Microsoft YaHei",
    englishFont: "Arial",
    englishFontSize: "48",
    chineseFontScale: "1.2",
    chineseColor: "#ffc928",
    englishColor: "#ffffff",
  },
  cinema: {
    chineseFont: "SimSun",
    englishFont: "Georgia",
    englishFontSize: "48",
    chineseFontScale: "1.2",
    chineseColor: "#fff0d0",
    englishColor: "#fff0d0",
  },
};
const boxStyles = {
  "glass-diamond": { color: "#708496", opacity: "0.68" },
  "deep-blue": { color: "#0a3161", opacity: "0.9" },
};

function applyStylePreset(presetName) {
  const preset = stylePresets[presetName];
  if (!preset) return;
  chineseFontInput.value = preset.chineseFont;
  englishFontInput.value = preset.englishFont;
  englishFontSizeInput.value = preset.englishFontSize;
  chineseFontScaleInput.value = preset.chineseFontScale;
  chineseColorInput.value = preset.chineseColor;
  englishColorInput.value = preset.englishColor;
}

styleInputs.forEach((input) => {
  input.addEventListener("change", () => applyStylePreset(input.value));
});

boxStyleInputs.forEach((input) => {
  input.addEventListener("change", () => {
    const preset = boxStyles[input.value];
    if (!preset) return;
    boxColorInput.value = preset.color;
    boxColorHexInput.value = preset.color;
    boxOpacityInput.value = preset.opacity;
  });
});

boxColorInput.addEventListener("input", () => {
  boxColorHexInput.value = boxColorInput.value.toUpperCase();
});
boxColorHexInput.addEventListener("input", () => {
  const color = boxColorHexInput.value.trim();
  if (/^#[0-9a-fA-F]{6}$/.test(color)) {
    boxColorInput.value = color;
  }
});

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

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const videoUrl = urlInput.value.trim();
  const location = runLocation.value;
  if (!videoUrl) return;
  const stylePreset = styleInputs.find((input) => input.checked)?.value ?? "classic";
  const boxDisplay = boxDisplayInputs.find((input) => input.checked)?.value ?? "always";
  const boxStyle = boxStyleInputs.find((input) => input.checked)?.value ?? "glass-diamond";
  const displayDescription = boxDisplay === "always"
    ? "字幕背景框会从视频开始持续到结束，适合遮挡原片横幅"
    : "字幕背景框只在字幕出现时显示";
  const boxStyleDescription = boxStyle === "deep-blue"
    ? "深蓝背景框"
    : "磨砂玻璃钻石质感背景框";
  const options = {
    burnIn: burnInInput.checked,
    stylePreset,
    boxDisplay,
    boxStyle,
    chineseFont: chineseFontInput.value.trim(),
    englishFont: englishFontInput.value.trim(),
    englishFontSize: englishFontSizeInput.value,
    chineseFontScale: chineseFontScaleInput.value,
    chineseColor: chineseColorInput.value,
    englishColor: englishColorInput.value,
    boxColor: boxColorInput.value,
    boxOpacity: boxOpacityInput.value,
    boxX: boxXInput.value,
    boxY: boxYInput.value,
    boxWidth: boxWidthInput.value,
    boxHeight: boxHeightInput.value,
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
