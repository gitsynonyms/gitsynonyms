const REPOSITORY = "gitsynonyms/gitsynonyms";
const WORKFLOW_URL = `https://github.com/${REPOSITORY}/actions/workflows/youtube-subtitles.yml`;
const SCRIPT_URL = `https://raw.githubusercontent.com/${REPOSITORY}/main/scripts/download_youtube_bilingual_srt.py`;

const form = document.querySelector("#download-form");
const urlInput = document.querySelector("#video-url");
const runLocation = document.querySelector("#run-location");
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

function makeCommand(videoUrl, location) {
  const quotedUrl = quotePowerShell(videoUrl);
  if (location === "local") {
    return [
      `$script = Join-Path $PWD 'download_youtube_bilingual_srt.py'`,
      `Invoke-WebRequest -Uri '${SCRIPT_URL}' -OutFile $script`,
      `& 'C:\\Users\\a2023\\anaconda3\\python.exe' $script ${quotedUrl} --languages zh-Hans,en-orig --output-dir 'D:\\MyDownload'`,
    ].join("; ");
  }
  return `gh workflow run youtube-subtitles.yml --repo ${REPOSITORY} --ref main -f source=${quotedUrl}`;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const videoUrl = urlInput.value.trim();
  const location = runLocation.value;
  if (!videoUrl) return;

  instructions.hidden = false;
  statusRegion.hidden = true;
  commandOutput.textContent = makeCommand(videoUrl, location);
  workflowLink.hidden = location !== "runner";
  workflowLink.href = WORKFLOW_URL;

  if (location === "local") {
    instructionsTitle.textContent = "本地 Anaconda 运行";
    instructionsCopy.textContent =
      "点击复制后，在 Windows PowerShell 中粘贴并运行。脚本会下载两种语言的字幕，并保存单语及双语 SRT 到 D:\\MyDownload；本地需要已安装 yt-dlp。";
  } else {
    instructionsTitle.textContent = "GitHub 云端 runner 运行";
    instructionsCopy.textContent =
      "复制命令后，在已通过 gh auth login 登录、且有仓库 Actions 写权限的终端执行；字幕抓取由 GitHub Actions runner 完成，输出可从 Actions 运行记录的 artifact 下载。也可打开 Actions 页面手动启动 workflow 并填入相同视频链接。无需在网页粘贴访问令牌。";
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
