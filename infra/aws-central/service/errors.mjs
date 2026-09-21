export class HttpError extends Error {
  constructor(status, code, message, details = null, { cause } = {}) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "HttpError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function asHttpError(error, fallback = "The service could not complete this request.") {
  if (error instanceof HttpError) return error;
  return new HttpError(500, "internal_error", fallback);
}
