const topicInput = document.querySelector("#topic");
const skillCommand = document.querySelector("#skill-command");
const cliCommand = document.querySelector("#cli-command");
const topicError = document.querySelector("#topic-error");
const copyStatus = document.querySelector("#copy-status");

function shellQuote(value) {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

function updateCommands() {
  const topic = topicInput.value.trim();
  const valid = topic.length > 0;
  topicError.hidden = valid;

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

topicInput.addEventListener("input", updateCommands);

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const output = document.getElementById(button.dataset.copy);
    try {
      await navigator.clipboard.writeText(output.textContent);
      copyStatus.textContent = "已复制。首次使用 CLI 时，请先按 preflight 指引设置 LAST30DAYS_PYTHON。";
    } catch (error) {
      copyStatus.textContent = "复制失败，请手动选择并复制上方命令。";
      console.error("Could not copy the Last30Days command.", error);
    }
  });
});

updateCommands();
