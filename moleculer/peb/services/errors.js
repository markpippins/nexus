export class ApiError extends Error {
  constructor(status, message, details) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

export function badRequest(message, details) {
  return new ApiError(400, message, details);
}

export function notFound(message) {
  return new ApiError(404, message || 'not found');
}

export function conflict(message, details) {
  return new ApiError(409, message, details);
}
