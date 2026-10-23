class AppError(Exception):
    def __init__(self, status_code: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def not_found(resource: str) -> AppError:
    return AppError(404, "NOT_FOUND", f"{resource} was not found")


def conflict(error: str, message: str) -> AppError:
    return AppError(409, error, message)

