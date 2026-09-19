export const modelConfig = Object.freeze({
  defaultModel: "gpt-5-mini",
  allowedModels: Object.freeze(["gpt-5-mini", "gpt-5.6"]),
  defaultReasoningEffort: "minimal",
  reasoningEffortByModel: Object.freeze({
    "gpt-5-mini": "minimal",
    "gpt-5.6": "none",
  }),
  defaultImageDetail: "high",
  originalImageDetail: "original",
  allowedImageDetails: Object.freeze(["high", "original"]),
  originalDetailModels: Object.freeze(["gpt-5.6"]),
  defaultLanguage: "en",
  allowedLanguages: Object.freeze(["en", "kn", "mr", "bn"]),
});
