"""İstek bağlamı: hangi terminalden geldiğini, parametre taşımadan log katmanına iletir."""
import contextvars
from contextlib import contextmanager

_terminal = contextvars.ContextVar("terminal_kodu", default="")


def terminal():
    """Şu anki isteğin terminal kodu; yoksa boş string."""
    return _terminal.get()


@contextmanager
def terminal_baglami(kod):
    jeton = _terminal.set(kod or "")
    try:
        yield
    finally:
        _terminal.reset(jeton)
