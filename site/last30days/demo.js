const skillTopicInput = document.querySelector("#skill-topic");
const skillTopicError = document.querySelector("#skill-topic-error");
const skillCommand = document.querySelector("#skill-command");
const cliCommand = document.querySelector("#cli-command");
const videoTopicInput = document.querySelector("#video-topic");
const videoTopicError = document.querySelector("#video-topic-error");
const videoCommand = document.querySelector("#video-skill-command");
const videoStatus = document.querySelector("#video-status");
const videoTitle = document.querySelector("#latest-video-title");
const videoLink = document.querySelector("#latest-video-link");
const captionLink = document.querySelector("#latest-caption-link");
const copyVideoButton = document.querySelector("#copy-video-command");

function shellQuote(value) {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

function updateSkillCommands() {
  const topic = skillTopicInput.value.trim();
  const valid = topic.length > 0;
  skillTopicError.hidden = valid;

  if (!valid) {
    skillCommand.textContent = "/last30days <topic>";
    cliCommand.textContent = "Enter a topic above to generate the command.";
    return;
  }

  skillCommand.textContent = `/last30days ${topic}`;
  cliCommand.textContent = [
    "git clone https://github.com/mvanhorn/last30days-skill.git",
    "cd last30days-skill",
    "python3 skills/last30days/scripts/last30days.py --preflight",
    `"$LAST30DAYS_PYTHON" skills/last30days/scripts/last30days.py ${shellQuote(topic)} --emit=json`,
  ].join("\n");
}

function updateVideoCommand() {
  const topic = videoTopicInput.value.trim();
  const valid = topic.length > 0;
  videoTopicError.hidden = valid;
  copyVideoButton.disabled = !valid || videoTopicInput.disabled;
  videoCommand.textContent = valid
    ? `/last30days ${topic}`
    : "Enter a person, news item, or topic from the video.";
}

function getLatestVideoDetails(page, pageUrl) {
  const title = page.querySelector("#video-title")?.textContent.trim();
  if (!title) {
    throw new Error("The latest video page does not include a video title.");
  }

  const source = Array.from(page.querySelectorAll("a[href]"))
    .map((anchor) => new URL(anchor.getAttribute("href"), pageUrl))
    .find((url) => {
      return url.hostname === "www.youtube.com"
        && url.pathname === "/watch"
        && /^[\w-]{11}$/.test(url.searchParams.get("v") || "");
    });
  if (!source) {
    throw new Error("The latest video page does not include a valid YouTube link.");
  }

  const videoId = source.searchParams.get("v");
  const transcript = Array.from(page.querySelectorAll("a[href]"))
    .map((anchor) => new URL(anchor.getAttribute("href"), pageUrl))
    .find((url) => url.pathname.endsWith(`/${videoId}.en-orig.vtt`));

  return { title, source, transcript };
}

async function loadLatestVideo() {
  const pageUrl = new URL("../latest.html", window.location.href);
  try {
    const response = await fetch(pageUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Latest video page returned HTTP ${response.status}.`);
    }
    const html = await response.text();
    const page = new DOMParser().parseFromString(html, "text/html");
    const details = getLatestVideoDetails(page, pageUrl);

    videoTitle.textContent = details.title;
    videoLink.href = details.source.href;
    videoLink.hidden = false;
    if (details.transcript) {
      captionLink.href = details.transcript.href;
      captionLink.hidden = false;
    } else {
      videoStatus.textContent = "已载入视频；本站暂未发布该视频的英文字幕链接。";
    }

    videoTopicInput.value = details.title;
    videoTopicInput.disabled = false;
    copyVideoButton.disabled = false;
    if (details.transcript) {
      videoStatus.textContent = "已载入本站最新归档视频。可查看英文字幕，选取其中人物、新闻或话题作为研究对象。";
    }
    updateVideoCommand();
  } catch (error) {
    videoStatus.textContent = "无法载入最新视频信息，请稍后重试或查看“最新成片”页面。";
    console.error("Could not load the latest White House video.", error);
  }
}

skillTopicInput.addEventListener("input", updateSkillCommands);
videoTopicInput.addEventListener("input", updateVideoCommand);

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const output = document.getElementById(button.dataset.copy);
    const status = document.getElementById(button.dataset.status);
    try {
      await navigator.clipboard.writeText(output.textContent);
      status.textContent = button.dataset.copy === "cli-command"
        ? "已复制。首次使用 CLI 时，请先按 preflight 指引设置 LAST30DAYS_PYTHON。"
        : "已复制调用方式。";
    } catch (error) {
      status.textContent = "复制失败，请手动选择并复制上方命令。";
      console.error("Could not copy the Last30Days command.", error);
    }
  });
});

updateSkillCommands();
updateVideoCommand();
loadLatestVideo();
