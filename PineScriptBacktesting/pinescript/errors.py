class PineError(Exception):
    """Base class for all Pine Script conversion/execution errors."""


class PineSyntaxError(PineError):
    """Raised when the source cannot be tokenized or parsed."""


class PineUnsupportedError(PineError):
    """Raised when the source uses a construct this engine doesn't support.

    Typical triggers: `request.security()`, `import` of a custom library,
    matrix/map types, or other advanced Pine v5/v6 features. See the
    project README's "Known limitations" section.
    """


class PineRuntimeError(PineError):
    """Raised for errors while executing a converted strategy bar-by-bar."""
