const STORAGE_KEY = "gitsynonyms.captionSettings.v1";
const form = document.querySelector("#settings-form");
const colorInput = document.querySelector("#box-color");
const colorHexInput = document.querySelector("#box-color-hex");
const opacityInput = document.querySelector("#box-opacity");
const opacityValue = document.querySelector("#box-opacity-value");
const previewBox = document.querySelector("#preview-caption-box");
const previewEnglish = document.querySelector("#preview-english");
const previewChinese = document.querySelector("#preview-chinese");
const previewSummary = document.querySelector("#preview-summary");
const saveStatus = document.querySelector("#save-status");

const defaults = {
  stylePreset: "classic",
  boxDisplay: "always",
  boxStyle: "glass-diamond",
  chineseFont: "Microsoft YaHei",
  englishFont: "Arial",
  englishFontSize: "48",
  chineseFontScale: "1.2",
  chineseColor: "#ffc928",
  englishColor: "#ffffff",
  boxColor: "#708496",
  boxOpacity: "0.68",
  boxX: "66",
  boxY: "883",
  boxWidth: "1786",
  boxHeight: "139",
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
};

function readSettings() {
  const saved = window.localStorage.getItem(STORAGE_KEY);
  const settings = saved ? { ...defaults, ...JSON.parse(saved) } : { ...defaults };
  if (settings.boxStyle === "presidential-blue" && typeof settings.boxColor === "string" && settings.boxColor.toLowerCase() === "#0d132d") {
    settings.boxColor = "#0f1647";
  }
  return settings;
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

function updatePreview() {
  const settings = getFormSettings();
  const order = settings.stylePreset === "cinema"
    ? ["english", "chinese"]
    : ["chinese", "english"];
  previewBox.classList.toggle("blue-preview", settings.boxStyle === "presidential-blue");
  previewBox.classList.toggle("glass-preview", settings.boxStyle === "glass-diamond");
  previewBox.classList.toggle("caption-only-preview", settings.boxDisplay === "with-captions");
  previewBox.style.setProperty("--preview-color", settings.boxColor);
  previewBox.style.setProperty("--preview-opacity", settings.boxOpacity);
  previewBox.style.setProperty("--preview-chinese-color", settings.chineseColor);
  previewBox.style.setProperty("--preview-english-color", settings.englishColor);
  previewBox.style.setProperty("--preview-chinese-font", `"${settings.chineseFont}", sans-serif`);
  previewBox.style.setProperty("--preview-english-font", `"${settings.englishFont}", sans-serif`);
  previewBox.style.setProperty("--preview-chinese-size", `${Math.min(Number(settings.chineseFontScale) * 18, 36)}px`);
  previewBox.style.setProperty("--preview-english-size", `${Math.min(Number(settings.englishFontSize) * 0.375, 28)}px`);
  previewBox.style.setProperty("--preview-width", `${Math.min(Number(settings.boxWidth) / 19.2, 100)}%`);
  previewBox.style.setProperty("--preview-height", `${Math.min(Number(settings.boxHeight) / 2.4, 100)}%`);
  previewBox.replaceChildren(...order.map((language) => {
    const text = language === "english" ? previewEnglish : previewChinese;
    return text;
  }));
  opacityValue.value = settings.boxOpacity;
  opacityValue.textContent = settings.boxOpacity;
  previewSummary.textContent =
    `${settings.boxDisplay === "always" ? "全程显示" : "仅字幕期间显示"} · ` +
    `${settings.boxStyle === "presidential-blue" ? "总统蓝" : "磨砂玻璃钻石"} · ` +
    `${settings.boxX}, ${settings.boxY} · ${settings.boxWidth} × ${settings.boxHeight}`;
}

function saveSettings(event) {
  event.preventDefault();
  if (!form.reportValidity()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(getFormSettings()));
    saveStatus.textContent = "设置已保存，并会应用到字幕生成页面。";
    window.setTimeout(() => {
      saveStatus.textContent = "";
    }, 3500);
  } catch (error) {
    saveStatus.textContent = `保存失败：${error.message}`;
  }
}

try {
  applySettings(readSettings());
} catch (error) {
  saveStatus.textContent = `读取已保存设置失败：${error.message}`;
  applySettings(defaults);
}

form.addEventListener("change", (event) => {
  if (event.target.name === "style-preset") {
    applyTextPreset(event.target.value);
  } else if (event.target.name === "box-style") {
    const preset = backgroundValues[event.target.value];
    if (preset) {
      colorInput.value = preset.boxColor;
      colorHexInput.value = preset.boxColor;
      opacityInput.value = preset.boxOpacity;
    }
  }
  updatePreview();
});
form.addEventListener("input", updatePreview);
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
