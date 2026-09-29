export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
    public code = "request_failed",
    public details?: unknown,
  ) {
    super(message);
  }
}
