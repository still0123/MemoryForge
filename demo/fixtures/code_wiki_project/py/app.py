from py.helpers import normalize


def local(value: str) -> str:
    return value.lower()


def run_local(value: str) -> str:
    return local(value)


def run_imported(value: str) -> str:
    return normalize(value)
