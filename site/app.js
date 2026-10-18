const OWNER = "gitsynonyms";
const REPOSITORY = "gitsynonyms";
const WORKFLOW = "youtube-subtitles.yml";
const API_ROOT = `https://api.github.com/repos/${OWNER}/${REPOSITORY}`;
const RUN_TIMEOUT_MS = 15 * 60 * 1000;
const POLL_INTERVAL_MS = 4000;

const form = document.querySelector("#download-form");
const urlInput = document.querySelector("#video-url");
const tokenInput = document.querySelector("#github-token");
const submitButton = document.querySelector("#submit-button");
const statusRegion = document.querySelector("#status");
const resultsRegion = document.querySelector("#results");
const objectUrls = new Set();

function setStatus(message, isError = false, link = "") {
  statusRegion.replaceChildren();
  statusRegion.hidden = false;
  statusRegion.classList.toggle("error", isError);
  statusRegion.append(document.createTextNode(message));
  if (link) {
    statusRegion.append(document.createTextNode(" "));
    const anchor = document.createElement("a");
    anchor.href = link;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    anchor.textContent = "查看 GitHub Actions 运行记录";
    statusRegion.append(anchor);
  }
}

async function githubRequest(path, token, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...options.headers,
    },
  });
  if (!response.ok) {
    const details = await response.json().catch(() => ({}));
    const error = new Error(details.message || `GitHub API 返回 HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response;
}

function read16(view, offset) {
  return view.getUint16(offset, true);
}

function read32(view, offset) {
  return view.getUint32(offset, true);
}

async function unzipSrtFiles(blob) {
  const bytes = await blob.arrayBuffer();
  const view = new DataView(bytes);
  let endRecord = -1;
  for (let offset = bytes.byteLength - 22; offset >= Math.max(0, bytes.byteLength - 65557); offset -= 1) {
    if (read32(view, offset) === 0x06054b50) {
      endRecord = offset;
      break;
    }
  }
  if (endRecord < 0) throw new Error("GitHub 返回的 artifact 不是有效 ZIP 文件。");

  const entries = read16(view, endRecord + 10);
  let cursor = read32(view, endRecord + 16);
  const decoder = new TextDecoder("utf-8");
  const files = [];

  for (let index = 0; index < entries; index += 1) {
    if (read32(view, cursor) !== 0x02014b50) throw new Error("无法读取字幕压缩包目录。");
    const method = read16(view, cursor + 10);
    const compressedSize = read32(view, cursor + 20);
    const nameLength = read16(view, cursor + 28);
    const extraLength = read16(view, cursor + 30);
    const commentLength = read16(view, cursor + 32);
    const localOffset = read32(view, cursor + 42);
    const nameStart = cursor + 46;
    const name = decoder.decode(new Uint8Array(bytes, nameStart, nameLength));
    cursor = nameStart + nameLength + extraLength + commentLength;

    if (!name.toLowerCase().endsWith(".srt")) continue;
    if (read32(view, localOffset) !== 0x04034b50) throw new Error("无法读取字幕文件数据。");
    const localNameLength = read16(view, localOffset + 26);
    const localExtraLength = read16(view, localOffset + 28);
    const dataStart = localOffset + 30 + localNameLength + localExtraLength;
    const compressed = bytes.slice(dataStart, dataStart + compressedSize);
    let content;
    if (method === 0) {
      content = compressed;
    } else if (method === 8 && typeof DecompressionStream !== "undefined") {
      const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
      content = await new Response(stream).arrayBuffer();
    } else {
      throw new Error("当前浏览器无法解压 Actions artifact，请使用运行记录页面下载 ZIP。");
    }
    files.push({ name: name.split("/").pop(), text: decoder.decode(content) });
  }
  if (!files.length) throw new Error("artifact 中没有找到 SRT 文件。");
  return files;
}

function renderFiles(files) {
  resultsRegion.replaceChildren();
  for (const file of files.sort((a, b) => a.name.localeCompare(b.name))) {
    const card = document.createElement("article");
    card.className = "file";
    const header = document.createElement("div");
    header.className = "file-head";
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = file.name;
    const link = document.createElement("a");
    const objectUrl = URL.createObjectURL(new Blob([file.text], { type: "application/x-subrip;charset=utf-8" }));
    objectUrls.add(objectUrl);
    link.href = objectUrl;
    link.download = file.name;
    link.textContent = "下载 SRT";
    header.append(name, link);

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "预览字幕";
    const preview = document.createElement("pre");
    preview.textContent = file.text;
    details.append(summary, preview);
    card.append(header, details);
    resultsRegion.append(card);
  }
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function waitForRun(token, requestId) {
  const expectedTitle = `YouTube subtitles ${requestId}`;
  const deadline = Date.now() + RUN_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const response = await githubRequest(
      `/actions/workflows/${WORKFLOW}/runs?branch=main&event=workflow_dispatch&per_page=20`,
      token,
    );
    const payload = await response.json();
    const run = payload.workflow_runs.find(
      (item) => item.display_title === expectedTitle || item.run_name === expectedTitle,
    );
    if (run?.status === "completed") return run;
    await wait(POLL_INTERVAL_MS);
  }
  throw new Error("等待云端任务超时；可以稍后从 GitHub Actions 运行记录查看状态。");
}

async function loadArtifact(token, run) {
  const response = await githubRequest(`/actions/runs/${run.id}/artifacts`, token);
  const { artifacts } = await response.json();
  const artifact = artifacts.find((item) => item.name === `youtube-subtitles-${run.request_id}`);
  if (!artifact) throw new Error("云端任务完成，但未找到字幕 artifact。");
  const archive = await githubRequest(`/actions/artifacts/${artifact.id}/zip`, token);
  return unzipSrtFiles(await archive.blob());
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  for (const objectUrl of objectUrls) URL.revokeObjectURL(objectUrl);
  objectUrls.clear();
  resultsRegion.replaceChildren();
  const videoUrl = urlInput.value.trim();
  const token = tokenInput.value.trim();
  tokenInput.value = "";
  if (!token) {
    setStatus("请提供临时 GitHub token。", true);
    return;
  }

  submitButton.disabled = true;
  const requestId = crypto.randomUUID();
  let runUrl = "";
  try {
    setStatus("正在提交云端任务……");
    await githubRequest(`/actions/workflows/${WORKFLOW}/dispatches`, token, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ref: "main",
        inputs: { source: videoUrl, request_id: requestId },
      }),
    });

    setStatus("字幕正在 GitHub Actions 云端生成，请稍候……");
    const run = await waitForRun(token, requestId);
    runUrl = run.html_url;
    if (run.conclusion !== "success") {
      throw new Error(`云端任务结束状态：${run.conclusion || "unknown"}`);
    }

    setStatus("正在读取字幕文件……");
    const files = await loadArtifact(token, { ...run, request_id: requestId });
    renderFiles(files);
    setStatus(`完成：已生成并加载 ${files.length} 个 SRT 文件。`, false, runUrl);
  } catch (error) {
    const fallbackLink = runUrl || "https://github.com/gitsynonyms/gitsynonyms/actions/workflows/youtube-subtitles.yml";
    setStatus(
      `${error.message}${error.status === 401 || error.status === 403 ? " 请确认 PAT 对此仓库具有 Actions: Read and write 权限。" : ""}`,
      true,
      fallbackLink,
    );
  } finally {
    submitButton.disabled = false;
  }
});
