import { NextFunction, Request, Response } from 'express';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = 'BAD_REQUEST',
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** Wraps an async handler so rejected promises reach the error middleware. */
export const asyncHandler =
  (fn: (req: Request, res: Response) => Promise<unknown>) =>
  (req: Request, res: Response, next: NextFunction) => {
    fn(req, res).catch(next);
  };
