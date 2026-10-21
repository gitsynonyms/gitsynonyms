(() => {
  const STORAGE_KEY = "gitsynonyms.captionSettings.v1";
  const DEFAULTS = Object.freeze({
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
  });

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

  function normalize(settings) {
    const normalized = { ...settings };
    if (
      normalized.boxStyle === "presidential-blue" &&
      typeof normalized.boxColor === "string" &&
      /^#[0-9a-f]{6}$/i.test(normalized.boxColor) &&
      typeof normalized.chineseColor === "string" &&
      /^#[0-9a-f]{6}$/i.test(normalized.chineseColor) &&
      contrastRatio(normalized.boxColor, normalized.chineseColor) < 4.5
    ) {
      normalized.chineseColor = "#ffc928";
    }
    if (
      normalized.boxStyle === "presidential-blue" &&
      typeof normalized.boxColor === "string" &&
      /^#[0-9a-f]{6}$/i.test(normalized.boxColor) &&
      typeof normalized.englishColor === "string" &&
      /^#[0-9a-f]{6}$/i.test(normalized.englishColor) &&
      contrastRatio(normalized.boxColor, normalized.englishColor) < 4.5
    ) {
      normalized.englishColor = "#ffffff";
    }
    return normalized;
  }

  function read() {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    const settings = saved ? { ...DEFAULTS, ...JSON.parse(saved) } : { ...DEFAULTS };
    if (
      settings.boxStyle === "presidential-blue" &&
      typeof settings.boxColor === "string" &&
      settings.boxColor.toLowerCase() === "#0d132d"
    ) {
      settings.boxColor = "#0f1647";
    }
    return normalize(settings);
  }

  function write(settings) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }

  window.CaptionSettings = Object.freeze({
    storageKey: STORAGE_KEY,
    defaults: DEFAULTS,
    read,
    write,
    normalize,
  });
})();
