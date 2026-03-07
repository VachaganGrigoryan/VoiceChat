from app.core.http import ErrorResponse

DEFAULT_ERROR_CODES = (400, 401, 404, 422, 500)

COMMON_ERROR_RESPONSES = {
    400: {
        "model": ErrorResponse,
        "description": "Bad Request. The request is syntactically valid but business validation failed.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": "The request could not be processed.",
                        "details": None,
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
    401: {
        "model": ErrorResponse,
        "description": "Unauthorized. Authentication is missing, invalid, or expired.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Missing or invalid authentication token.",
                        "details": None,
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
    403: {
        "model": ErrorResponse,
        "description": "Forbidden. The authenticated user is not allowed to perform this action.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "You do not have permission to perform this action.",
                        "details": None,
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
    404: {
        "model": ErrorResponse,
        "description": "Not Found. The requested resource does not exist.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "Requested resource was not found.",
                        "details": None,
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
    409: {
        "model": ErrorResponse,
        "description": "Conflict. The request conflicts with the current state of the resource.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "CONFLICT",
                        "message": "A resource with the same identifier already exists.",
                        "details": None,
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
    422: {
        "model": ErrorResponse,
        "description": "Validation Error. One or more request fields failed validation.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Request validation failed.",
                        "details": [
                            {
                                "field": "email",
                                "message": "value is not a valid email address",
                                "type": "value_error",
                            }
                        ],
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
    429: {
        "model": ErrorResponse,
        "description": "Too Many Requests. Rate limit exceeded.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests. Please try again later.",
                        "details": None,
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
    500: {
        "model": ErrorResponse,
        "description": "Internal Server Error. An unexpected server error occurred.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "Internal server error.",
                        "details": None,
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
    502: {
        "model": ErrorResponse,
        "description": "Bad Gateway. An upstream dependency failed.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": "UPSTREAM_ERROR",
                        "message": "Failed to communicate with upstream service.",
                        "details": None,
                    },
                    "request_id": "req_1234567890",
                }
            }
        },
    },
}

DEFAULT_ERROR_RESPONSES = {
    code: COMMON_ERROR_RESPONSES[code]
    for code in DEFAULT_ERROR_CODES
}


def build_error_responses(*codes: int):
    return {code: COMMON_ERROR_RESPONSES[code] for code in codes}
