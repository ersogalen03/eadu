# Part of Eadu. See LICENSE file for full copyright and licensing details.


class EaduConnectionError(Exception):
    """Raised when an eadu remote call fails due to network or connection issues.

    Callers can catch this to queue the call for later retry instead of
    letting the error propagate and blocking local operations.
    """
