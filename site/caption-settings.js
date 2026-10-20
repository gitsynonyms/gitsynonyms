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
    return settings;
  }

  function write(settings) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }

  window.CaptionSettings = Object.freeze({
    storageKey: STORAGE_KEY,
    defaults: DEFAULTS,
    read,
    write,
  });
})();
