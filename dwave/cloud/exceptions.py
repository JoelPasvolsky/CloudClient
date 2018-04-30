class ConfigFileError(Exception):
    """Base exception for all configuration file processing errors."""

class ConfigFileReadError(ConfigFileError):
    """Non-existing or unreadable configuration file specified or implied."""

class ConfigFileParseError(ConfigFileError):
    """Invalid format of configuration file."""


class SolverError(Exception):
    """Generic base class for all solver-related errors."""

class SolverFailureError(SolverError):
    """Remote failure when calling a solver."""

class SolverAuthenticationError(SolverError):
    """Authentication error when calling a solver."""

    def __init__(self):
        super(SolverAuthenticationError, self).__init__("Token not accepted for that action.")

class UnsupportedSolverError(SolverError):
    """Solver received from the API is unsupported by the client."""


class CanceledFutureError(Exception):
    """Attempted read from a canceled Future object."""

    def __init__(self):
        super(CanceledFutureError, self).__init__("An error occurred reading results from a canceled request")


class InvalidAPIResponseError(Exception):
    """Invalid/unexpected response from D-Wave Solver API received."""
