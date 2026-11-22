import importlib.util
import inspect
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_MARK = "__routine__"
_KINDS = (int, float, str)
_ids = itertools.count()


class RoutineError(ValueError):
    """A pack's routines file cannot be loaded or declares a bad routine."""


class RoutineStopped(Exception):
    """Raised by a routine that cannot confirm a step. `step` names the step
    reached; the message says what was seen instead."""

    def __init__(self, message: str, step: str = "") -> None:
        super().__init__(message)
        self.step = step


@dataclass(frozen=True)
class Param:
    """One argument of a routine. Numbers are limited by `low`/`high`
    (inclusive), text by `choices` when given."""

    kind: type
    low: float | None = None
    high: float | None = None
    choices: tuple[str, ...] = ()

    def schema(self) -> dict:
        if self.kind is str:
            out: dict = {"type": "string"}
            if self.choices:
                out["enum"] = list(self.choices)
            return out
        out = {"type": "integer" if self.kind is int else "number"}
        if self.low is not None:
            out["minimum"] = self.low
        if self.high is not None:
            out["maximum"] = self.high
        return out

    def check(self, name: str, value: object):
        if self.kind is str:
            if not isinstance(value, str):
                raise ValueError(f"`{name}` must be text, got {value!r}")
            if self.choices and value not in self.choices:
                raise ValueError(
                    f"`{name}` must be one of {', '.join(self.choices)}, got {value!r}"
                )
            return value
        accepted = (int,) if self.kind is int else (int, float)
        if isinstance(value, bool) or not isinstance(value, accepted):
            wanted = "a whole number" if self.kind is int else "a number"
            # A wrong argument from the model, reported like the other bad values.
            raise ValueError(f"`{name}` must be {wanted}, got {value!r}")  # noqa: TRY004
        if (self.low is not None and value < self.low) or (
            self.high is not None and value > self.high
        ):
            raise ValueError(
                f"`{name}` must be between {self.low} and {self.high}, got {value!r}"
            )
        return self.kind(value)


@dataclass(frozen=True)
class Routine:
    """A fixed sequence a pack offers: `run(hands, **arguments)` returns the
    text reported back, or raises RoutineStopped."""

    name: str
    description: str
    run: Callable[..., str]
    params: dict[str, Param] = field(default_factory=dict)

    def check_args(self, args: dict) -> dict:
        """Return `args` validated; raise ValueError naming what is wrong. Every
        parameter is required and no other key is accepted."""
        unknown = sorted(str(key) for key in args.keys() - self.params.keys())
        missing = sorted(self.params.keys() - args.keys())
        if unknown or missing:
            raise ValueError(
                f"routine {self.name}: missing arguments {missing}, "
                f"unknown arguments {unknown}"
            )
        return {
            name: param.check(name, args[name]) for name, param in self.params.items()
        }


def routine(description: str, **params: Param):
    """Mark a function of a pack's routines file as a routine. Its name is the
    function's name; its keyword arguments are `params`."""

    def mark(fn):
        setattr(fn, _MARK, (description, params))
        return fn

    return mark


def load_routines(path: Path) -> dict[str, Routine]:
    """Import the routines file at `path` and return its routines by name, in
    the order they are defined. Raises RoutineError naming the file."""
    spec = importlib.util.spec_from_file_location(f"_pack_routines_{next(_ids)}", path)
    if spec is None or spec.loader is None:
        raise RoutineError(f"{path}: not a Python file")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise RoutineError(f"{path}: {type(exc).__name__}: {exc}") from exc
    found: dict[str, Routine] = {}
    for name, value in vars(module).items():
        mark = getattr(value, _MARK, None)
        if mark is None or getattr(value, "__module__", None) != module.__name__:
            continue
        description, params = mark
        if not callable(value) or not isinstance(description, str) or not description:
            raise RoutineError(f"{path}: routine {name!r} needs a description")
        for key, param in params.items():
            if not isinstance(param, Param) or param.kind not in _KINDS:
                raise RoutineError(
                    f"{path}: routine {name!r} argument {key!r} must be a Param of "
                    "int, float or str"
                )
        expected = ["hands", *params]
        try:
            takes = [
                p.name
                for p in inspect.signature(value).parameters.values()
                if p.kind
                in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
                and p.default is p.empty
            ]
            accepts = list(inspect.signature(value).parameters)
        except (TypeError, ValueError) as exc:
            raise RoutineError(f"{path}: routine {name!r}: {exc}") from exc
        if takes != expected or accepts != expected:
            # Found now and not when the routine is first called, which for
            # an on_stop routine is when a run is already ending.
            raise RoutineError(
                f"{path}: routine {name!r} must be defined as "
                f"{name}({', '.join(expected)}), got ({', '.join(accepts)})"
            )
        found[name] = Routine(name, description, value, dict(params))
    return found
