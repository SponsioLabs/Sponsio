

# -- which customer a run belongs to ----------------------------------------


def test_the_project_comes_from_the_environment_when_the_call_does_not_say(monkeypatch):
    """A platform ships one image per agent and varies the environment.
    Requiring the customer's name in the call meant the image differed per
    customer, or the caller wrote the plumbing themselves."""
    from sponsio.cloud.client import read_project

    monkeypatch.setenv("SPONSIO_PROJECT", "fabrikam-health")
    assert read_project() == "fabrikam-health"


def test_an_unset_or_blank_project_is_none(monkeypatch):
    from sponsio.cloud.client import read_project

    monkeypatch.delenv("SPONSIO_PROJECT", raising=False)
    assert read_project() is None
    monkeypatch.setenv("SPONSIO_PROJECT", "   ")
    assert read_project() is None


def test_an_explicit_project_still_wins(monkeypatch):
    """Code that names the customer is being deliberate; the environment
    must not silently redirect its runs."""
    import sponsio
    from sponsio.bridge import session as bridge

    monkeypatch.setenv("SPONSIO_PROJECT", "from-the-environment")
    guard = sponsio.Sponsio(agent_id="a", contracts=["tool `x` at most 1 times"], verbose=False)
    run = bridge.attach(guard, project="from-the-call", auto=False)
    assert run.project == "from-the-call"


def test_the_environment_is_used_when_the_call_is_silent(monkeypatch):
    import sponsio
    from sponsio.bridge import session as bridge

    monkeypatch.setenv("SPONSIO_PROJECT", "fabrikam-health")
    guard = sponsio.Sponsio(agent_id="a", contracts=["tool `x` at most 1 times"], verbose=False)
    run = bridge.attach(guard, auto=False)
    assert run.project == "fabrikam-health"


def test_neither_falls_back_to_default(monkeypatch):
    import sponsio
    from sponsio.bridge import session as bridge

    monkeypatch.delenv("SPONSIO_PROJECT", raising=False)
    guard = sponsio.Sponsio(agent_id="a", contracts=["tool `x` at most 1 times"], verbose=False)
    run = bridge.attach(guard, auto=False)
    assert run.project == "default"
