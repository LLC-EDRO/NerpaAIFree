/** Cloud defaults mirror Nerpa's author-template configuration.
 * The lab's Open WebUI selection is applied separately in llm.ts. */
export function labAuthorEnvironment(env: NodeJS.ProcessEnv = process.env) {
  const model = env.PRESENTATION_AUTHOR_MODEL || "gpt-6-luna";
  return {
    LLM_PROVIDER: "openai",
    OUTLINE_PROVIDER: "deepseek",
    DEEPSEEK_MODEL: env.PRESENTATION_AUTHOR_PLAN_MODEL || "deepseek-flash",
    OPENAI_MODEL_MAIN: model,
    VISION_MODEL: env.PRESENTATION_AUTHOR_VISION_MODEL || model,
    OPENAI_SEARCH_MODEL: env.PRESENTATION_AUTHOR_SEARCH_MODEL || model,
  };
}
