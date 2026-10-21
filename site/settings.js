const sharedSettings = window.CaptionSettings;
const defaults = sharedSettings.defaults;
const STORAGE_KEY = sharedSettings.storageKey;
const form = document.querySelector("#settings-form");
const colorInput = document.querySelector("#box-color");
const colorHexInput = document.querySelector("#box-color-hex");
const opacityInput = document.querySelector("#box-opacity");
const opacityValue = document.querySelector("#box-opacity-value");
const previewBox = document.querySelector("#preview-caption-box");
const previewEnglish = document.querySelector("#preview-english");
const previewChinese = document.querySelector("#preview-chinese");
const previewSummary = document.querySelector("#preview-summary");
const contrastStatus = document.querySelector("#caption-contrast-status");
const saveStatus = document.querySelector("#save-status");

const comfortPalettes = {
  day: { background: "#F8F9FA", text: "#2B2D42" },
  night: { background: "#1A1A1A", text: "#D4D4D4" },
  paper: { background: "#F4ECD8", text: "#1C2A38" },
  plaque: { background: "#493016", text: "#FFE4A3" },
};

const presetValues = {
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

const backgroundValues = {
  "glass-diamond": { boxColor: "#708496", boxOpacity: "0.68" },
  "presidential-blue": { boxColor: "#0f1647", boxOpacity: "0.9" },
  "solid-color": { boxColor: "#708496", boxOpacity: "1" },
};

function readSettings() {
  return sharedSettings.read();
}

function getFormSettings() {
  const value = (name) => form.elements.namedItem(name).value;
  const checked = (name) => form.querySelector(`input[name="${name}"]:checked`).value;
  return {
    stylePreset: checked("style-preset"),
    boxDisplay: checked("box-display"),
    boxStyle: checked("box-style"),
    chineseFont: value("chinese-font").trim(),
    englishFont: value("english-font").trim(),
    englishFontSize: value("english-font-size"),
    chineseFontScale: value("chinese-font-scale"),
    chineseColor: value("chinese-color"),
    englishColor: value("english-color"),
    boxColor: colorHexInput.value.trim(),
    boxOpacity: value("box-opacity"),
    boxX: value("box-x"),
    boxY: value("box-y"),
    boxWidth: value("box-width"),
    boxHeight: value("box-height"),
  };
}

function setValue(name, value) {
  const control = form.elements.namedItem(name);
  if (control instanceof RadioNodeList) {
    const radio = form.querySelector(`input[name="${name}"][value="${value}"]`);
    if (radio) radio.checked = true;
    return;
  }
  control.value = value;
}

function applySettings(settings) {
  for (const name of ["style-preset", "box-display", "box-style"]) {
    const key = name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    setValue(name, settings[key]);
  }
  const fields = {
    "chinese-font": "chineseFont",
    "english-font": "englishFont",
    "english-font-size": "englishFontSize",
    "chinese-font-scale": "chineseFontScale",
    "chinese-color": "chineseColor",
    "english-color": "englishColor",
    "box-color": "boxColor",
    "box-color-hex": "boxColor",
    "box-opacity": "boxOpacity",
    "box-x": "boxX",
    "box-y": "boxY",
    "box-width": "boxWidth",
    "box-height": "boxHeight",
  };
  Object.entries(fields).forEach(([id, key]) => {
    const element = document.getElementById(id);
    element.value = settings[key];
  });
  updatePreview();
}

function applyTextPreset(name) {
  const preset = presetValues[name];
  if (!preset) return;
  document.querySelector("#chinese-font").value = preset.chineseFont;
  document.querySelector("#english-font").value = preset.englishFont;
  document.querySelector("#english-font-size").value = preset.englishFontSize;
  document.querySelector("#chinese-font-scale").value = preset.chineseFontScale;
  document.querySelector("#chinese-color").value = preset.chineseColor;
  document.querySelector("#english-color").value = preset.englishColor;
}

function relativeLuminance(hexColor) {
  const channels = hexColor.slice(1).match(/.{2}/g).map((channel) => {
    const value = Number.parseInt(channel, 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrastRatio(background, text) {
  const luminance = [relativeLuminance(background), relativeLuminance(text)].sort((a, b) => b - a);
  return (luminance[0] + 0.05) / (luminance[1] + 0.05);
}

function updatePaletteContrast() {
  document.querySelectorAll("[data-contrast]").forEach((element) => {
    const [background, text] = element.dataset.contrast.split(",");
    element.textContent = `WCAG 对比度 ${contrastRatio(background, text).toFixed(2)}:1`;
  });
}

function updatePaletteSelection(settings = getFormSettings()) {
  const selected = Object.entries(comfortPalettes).find(([, palette]) =>
    settings.boxStyle === "solid-color" &&
    settings.boxOpacity === "1" &&
    settings.boxColor.toLowerCase() === palette.background.toLowerCase() &&
    settings.chineseColor.toLowerCase() === palette.text.toLowerCase() &&
    settings.englishColor.toLowerCase() === palette.text.toLowerCase()
  )?.[0];
  form.querySelectorAll('input[name="comfort-palette"]').forEach((input) => {
    input.checked = input.value === selected;
  });
}

function updateCaptionContrast(settings) {
  const chineseRatio = contrastRatio(settings.boxColor, settings.chineseColor);
  const englishRatio = contrastRatio(settings.boxColor, settings.englishColor);
  const minimumRatio = Math.min(chineseRatio, englishRatio);
  contrastStatus.textContent =
    `文字/背景对比度：中文 ${chineseRatio.toFixed(2)}:1，英文 ${englishRatio.toFixed(2)}:1。` +
    (minimumRatio < 4.5
      ? " 对比不足，深色背景建议使用金色中文和白色英文。"
      : " 对比度良好。");
  contrastStatus.classList.toggle("contrast-warning", minimumRatio < 4.5);
}

function applyComfortPalette(name) {
  const palette = comfortPalettes[name];
  if (!palette) return;
  form.querySelector('#box-style-solid').checked = true;
  colorInput.value = palette.background.toLowerCase();
  colorHexInput.value = palette.background;
  opacityInput.value = "1";
  document.querySelector("#chinese-color").value = palette.text.toLowerCase();
  document.querySelector("#english-color").value = palette.text.toLowerCase();
  updatePreview();
}

function updatePreview() {
  const settings = getFormSettings();
  const previewBounds = previewBox.parentElement.getBoundingClientRect();
  const previewWidth = previewBounds.width;
  const scale = previewWidth > 0 ? previewWidth / 1920 : 0.5;
  const previewHeight = Math.max(1, previewBounds.height);
  const minimumHeight = Math.min(48, previewHeight);
  const heightPercent = Math.max(
    Number(settings.boxHeight) / 10.8,
    minimumHeight / previewHeight * 100
  );
  const topPercent = Math.min(
    Number(settings.boxY) / 10.8,
    100 - heightPercent
  );
  const order = settings.stylePreset === "cinema"
    ? ["english", "chinese"]
    : ["chinese", "english"];
  previewBox.classList.toggle("blue-preview", settings.boxStyle === "presidential-blue");
  previewBox.classList.toggle("glass-preview", settings.boxStyle === "glass-diamond");
  previewBox.classList.toggle("solid-preview", settings.boxStyle === "solid-color");
  previewBox.classList.toggle("caption-only-preview", settings.boxDisplay === "with-captions");
  previewBox.style.setProperty("--preview-color", settings.boxColor);
  previewBox.style.setProperty("--preview-opacity", settings.boxOpacity);
  previewBox.style.setProperty("--preview-chinese-color", settings.chineseColor);
  previewBox.style.setProperty("--preview-english-color", settings.englishColor);
  previewBox.style.setProperty("--preview-chinese-font", `"${settings.chineseFont}", sans-serif`);
  previewBox.style.setProperty("--preview-english-font", `"${settings.englishFont}", sans-serif`);
  previewBox.style.setProperty("--preview-chinese-size", `${Math.max(8, Number(settings.englishFontSize) * Number(settings.chineseFontScale) * scale)}px`);
  previewBox.style.setProperty("--preview-english-size", `${Math.max(8, Number(settings.englishFontSize) * scale)}px`);
  previewBox.style.setProperty("--preview-left", `${Number(settings.boxX) / 19.2}%`);
  previewBox.style.setProperty("--preview-top", `${topPercent}%`);
  previewBox.style.setProperty("--preview-width", `${Number(settings.boxWidth) / 19.2}%`);
  previewBox.style.setProperty("--preview-height", `${heightPercent}%`);
  previewBox.replaceChildren(...order.map((language) => {
    const text = language === "english" ? previewEnglish : previewChinese;
    return text;
  }));
  opacityValue.value = settings.boxOpacity;
  opacityValue.textContent = settings.boxOpacity;
  updatePaletteSelection(settings);
  updateCaptionContrast(settings);
  previewSummary.textContent =
    `${settings.boxDisplay === "always" ? "全程显示" : "仅字幕期间显示"} · ` +
    `${settings.boxStyle === "presidential-blue" ? "总统蓝" : settings.boxStyle === "solid-color" ? "纯色背景" : "磨砂玻璃钻石"} · ` +
    `${settings.boxX}, ${settings.boxY} · ${settings.boxWidth} × ${settings.boxHeight}`;
}

function persistSettings(message = "已自动保存，字幕生成页会同步使用。") {
  try {
    sharedSettings.write(getFormSettings());
    saveStatus.textContent = message;
  } catch (error) {
    saveStatus.textContent = `保存失败：${error.message}`;
  }
}

function saveSettings(event) {
  event.preventDefault();
  if (form.reportValidity()) persistSettings("设置已保存，并与字幕生成页面同步。");
}

try {
  applySettings(readSettings());
  updatePaletteContrast();
} catch (error) {
  saveStatus.textContent = `读取已保存设置失败：${error.message}`;
  applySettings(defaults);
}

form.addEventListener("change", (event) => {
  if (event.target.name === "style-preset") {
    applyTextPreset(event.target.value);
  } else if (event.target.name === "comfort-palette") {
    applyComfortPalette(event.target.value);
  } else if (event.target.name === "box-style") {
    const preset = backgroundValues[event.target.value];
    if (preset) {
      colorInput.value = preset.boxColor;
      colorHexInput.value = preset.boxColor;
      opacityInput.value = preset.boxOpacity;
    }
    applySettings(sharedSettings.normalize(getFormSettings()));
  }
  updatePreview();
  persistSettings();
});
form.addEventListener("input", (event) => {
  if (["box-color-hex", "box-color", "box-opacity", "chinese-color", "english-color"].includes(event.target.id)) {
    form.querySelectorAll('input[name="comfort-palette"]').forEach((input) => {
      input.checked = false;
    });
  }
  updatePreview();
  persistSettings();
});
colorInput.addEventListener("input", () => {
  colorHexInput.value = colorInput.value.toUpperCase();
});
colorHexInput.addEventListener("input", () => {
  if (/^#[0-9a-fA-F]{6}$/.test(colorHexInput.value)) {
    colorInput.value = colorHexInput.value;
    updatePreview();
  }
});
form.addEventListener("submit", saveSettings);
window.addEventListener("resize", updatePreview);
window.addEventListener("storage", (event) => {
  if (event.key !== STORAGE_KEY || !event.newValue) return;
  try {
    applySettings(sharedSettings.read());
    saveStatus.textContent = "已从另一个页面同步字幕设置。";
  } catch (error) {
    saveStatus.textContent = `同步字幕设置失败：${error.message}`;
  }
});
