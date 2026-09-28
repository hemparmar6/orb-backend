"""Domain exceptions for the ORB AI backend.

Each exception maps to a stable machine-readable `code` and an HTTP status.
Global handlers in `app/api/errors.py` translate these into a consistent
JSON error envelope.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all application errors."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any | None = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details


# ---- 400s ------------------------------------------------------------------


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    message = "Bad request"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"
    message = "Validation failed"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication required"


class InvalidCredentialsError(UnauthorizedError):
    code = "invalid_credentials"
    message = "Email or password is incorrect"


class InvalidTokenError(UnauthorizedError):
    code = "invalid_token"
    message = "Invalid or expired token"


class InactiveUserError(UnauthorizedError):
    code = "inactive_user"
    message = "User account is inactive"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "Resource conflict"


class EmailAlreadyRegisteredError(ConflictError):
    code = "email_already_registered"
    message = "An account with this email already exists"


# ---- 500s ------------------------------------------------------------------


class InternalError(AppError):
    status_code = 500
    code = "internal_error"
    message = "Internal server error"


# ---- Trading engine ---------------------------------------------------------


class EngineError(AppError):
    status_code = 400
    code = "engine_error"
    message = "Trading engine error"


class StrategyNotFoundError(NotFoundError):
    code = "strategy_not_registered"
    message = "Strategy is not registered"


class SessionNotFoundError(NotFoundError):
    code = "engine_session_not_found"
    message = "Engine session not found"


class SessionAlreadyRunningError(ConflictError):
    code = "engine_session_already_running"
    message = "A trading session is already running"


class RiskRejectedError(EngineError):
    status_code = 422
    code = "risk_rejected"
    message = "Order rejected by risk engine"


class OrderNotFoundError(NotFoundError):
    code = "order_not_found"
    message = "Order not found"


class InvalidOrderError(BadRequestError):
    code = "invalid_order"
    message = "Order request is invalid"


# ---- Brokers (Module 3) ---------------------------------------------------


class BrokerError(AppError):
    status_code = 502
    code = "broker_error"
    message = "Broker communication error"


class BrokerNotImplementedError(BrokerError):
    status_code = 501
    code = "broker_not_implemented"
    message = "Broker adapter is not implemented yet"


class BrokerNotFoundError(NotFoundError):
    code = "broker_account_not_found"
    message = "Broker account not found"


class BrokerAlreadyConnectedError(ConflictError):
    code = "broker_already_connected"
    message = "This broker account is already connected"


class BrokerCredentialsInvalidError(BadRequestError):
    code = "broker_credentials_invalid"
    message = "The provided broker credentials are invalid"


class UnsupportedBrokerError(BadRequestError):
    code = "unsupported_broker"
    message = "This broker is not supported"


class BrokerRequiredForLiveModeError(BadRequestError):
    code = "broker_required_for_live_mode"
    message = "broker_account_id is required when execution_mode='live'"


class LiveMarketDataError(EngineError):
    """Raised (fail-closed) when a live session cannot resolve a real,
    authenticated market-data provider.

    Live execution must NEVER fall back to the mock provider. If the required
    real provider (e.g. Dhan / Kotak Neo) is missing, unavailable, invalid, or
    unauthenticated, we refuse to start the strategy.
    """

    status_code = 400
    code = "live_market_data_provider_unavailable"
    message = "Live execution requires a real, authenticated market-data provider"



class BillingConfigurationError(AppError):
    """Raised (fail-closed) when production billing is misconfigured.

    Production must NEVER silently fall back to mock billing. If a real
    processor (Razorpay / Stripe) is selected without valid credentials, or
    ``BILLING_PROVIDER=mock`` is used in production, we refuse rather than
    confirm payments on a fake provider.
    """

    status_code = 500
    code = "billing_misconfigured"
    message = "Billing is misconfigured for this environment"
