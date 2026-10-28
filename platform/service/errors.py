"""Stable error envelope for the formal API."""
from __future__ import annotations


class ApiError(Exception):
    """Raised for every expected failure.

    The response body is always ``{"code": ..., "message": ...}`` so a client
    can branch on a stable code instead of on prose.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def unauthorized(message: str = '未登录') -> ApiError:
    return ApiError(401, 'unauthorized', message)


def forbidden(message: str) -> ApiError:
    return ApiError(403, 'forbidden', message)


def conflict(message: str, code: str = 'conflict') -> ApiError:
    return ApiError(409, code, message)


def timeout(message: str) -> ApiError:
    return ApiError(408, 'time_budget_exceeded', message)


def unprocessable(message: str) -> ApiError:
    return ApiError(422, 'invalid_request', message)


def rate_limited(message: str = '请求过于频繁，请稍后再试') -> ApiError:
    return ApiError(429, 'rate_limited', message)
