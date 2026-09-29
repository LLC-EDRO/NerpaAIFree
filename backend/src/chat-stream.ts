/** Open WebUI can override the requested stream mode in model settings.
 * Accept either JSON or SSE; never mix reasoning tokens into the JSON answer. */
export async function readChatCompletion(response: Response): Promise<any> {
  if (!response.headers.get("content-type")?.includes("text/event-stream"))
    return response.json();
  if (!response.body) throw new Error("Missing chat stream");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "", content = "", refusal = "", finish: string | null = null;
  let ended = false, bytes = 0;
  const result: any = {};
  const event = (block: string) => {
    const data = block.split(/\r?\n/).filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).trimStart()).join("\n");
    if (!data) return;
    if (data.trim() === "[DONE]") { ended = true; return; }
    const chunk = JSON.parse(data);
    if (chunk.error) throw new Error("Provider reported an error inside chat stream");
    for (const key of ["id", "model", "service_tier", "usage"])
      if (chunk[key] != null) result[key] = chunk[key];
    const choice = chunk.choices?.find((c: any) => (c.index ?? 0) === 0);
    if (!choice) return;
    const delta = choice.delta || choice.message || {};
    if (typeof delta.content === "string") content += delta.content;
    if (typeof delta.refusal === "string") refusal += delta.refusal;
    if (choice.finish_reason != null) finish = choice.finish_reason;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 16 * 1024 * 1024) throw new Error("Chat stream exceeds size limit");
      pending += decoder.decode(value, { stream: true });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(pending))) {
        event(pending.slice(0, boundary.index));
        pending = pending.slice(boundary.index + boundary[0].length);
      }
      if (ended) break;
    }
    pending += decoder.decode();
    if (pending.trim()) event(pending);
    // An abruptly closed connection must not become a successful partial JSON.
    if (!ended && !finish) throw new Error("Incomplete chat stream");
    result.choices = [{ index: 0, finish_reason: finish, message: {
      role: "assistant", content, ...(refusal ? { refusal } : {}),
    } }];
    return result;
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
