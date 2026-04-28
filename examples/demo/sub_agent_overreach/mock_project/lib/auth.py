"""Authentication helpers — needs a TODO comment per spec."""


def login(username: str, password: str) -> bool:
    return username == "admin" and password == "admin"


def logout() -> None:
    pass


def is_authorized(role: str) -> bool:
    return role in {"admin", "user"}
