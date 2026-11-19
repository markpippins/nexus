import crypto from "crypto";

export type ErrorCode =
  | "INVALID_ARGUMENTS"
  | "TOOL_NOT_FOUND"
  | "INTERNAL_ERROR"
  | "FILE_NOT_FOUND"
  | "PLAN_NOT_FOUND"
  | "TITLE_MISMATCH"
  | "PARSE_ERROR"
  | "NEBULA_UNAVAILABLE";

export interface AppError {
  error: {
    code: string;
    message: string;
    details?: any;
    requestId: string;
  };
}

export function createError(
  code: ErrorCode,
  message: string,
  details?: any,
  requestId?: string,
): AppError {
  return {
    error: {
      code,
      message,
      details,
      requestId: requestId || crypto.randomUUID(),
    },
  };
}

export function createSuccess(
  result: any,
  requestId?: string,
): { result: any; requestId: string } {
  return {
    result,
    requestId: requestId || crypto.randomUUID(),
  };
}
