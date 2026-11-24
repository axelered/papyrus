import type { ProblemCode, ProblemIssue } from './models.ts'

/** Base of every error raised by a request */
export class PapyrusError extends Error {
  // on the prototype: an own field would show up in console output
  static {
    PapyrusError.prototype.name = 'PapyrusError'
  }
}

/** The server is unreachable or the connection was lost */
export class ConnectionFailed extends PapyrusError {
  static {
    ConnectionFailed.prototype.name = 'ConnectionFailed'
  }
}

/** The client side timeout expired */
export class TimedOut extends ConnectionFailed {
  static {
    TimedOut.prototype.name = 'TimedOut'
  }
}

/** A successful status whose body is not a Papyrus response of this SDK version */
export class InvalidResponse extends PapyrusError {
  static {
    InvalidResponse.prototype.name = 'InvalidResponse'
  }
}

export interface APIErrorInit {
  status: number
  code?: string | undefined
  detail?: string | undefined
  errors?: readonly ProblemIssue[] | undefined
  requestId?: string | undefined
  retryAfter?: number | undefined
}

/** Error response; branch on `code`, undefined when the body is not a Papyrus problem (e.g. from a proxy) */
export class APIError extends PapyrusError {
  static {
    APIError.prototype.name = 'APIError'
  }
  readonly status: number
  // keeps the known codes as editor suggestions without rejecting new ones
  readonly code: ProblemCode | (string & {}) | undefined
  readonly detail: string | undefined
  readonly errors: readonly ProblemIssue[]
  /** `X-Request-ID` of the response, to find the request in the server logs */
  readonly requestId: string | undefined
  /** Seconds */
  readonly retryAfter: number | undefined

  constructor({ status, code, detail, errors = [], requestId, retryAfter }: APIErrorInit) {
    const issues = errors.map((issue) => `${issue.loc.join('.')}: ${issue.msg}`).join('; ')
    super(`${status} ${code ?? 'error'}${detail ? `: ${detail}` : ''}${issues ? ` (${issues})` : ''}`)
    this.status = status
    this.code = code
    this.detail = detail
    this.errors = errors
    this.requestId = requestId
    this.retryAfter = retryAfter
  }
}
