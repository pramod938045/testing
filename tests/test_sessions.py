import time

from app.sessions import SessionStore


def counter_factory():
    state = {"n": 0}

    def make():
        state["n"] += 1
        return f"agent-{state['n']}"

    return make


def test_same_key_returns_the_same_session():
    store = SessionStore(counter_factory())
    assert store.get("a") == "agent-1"
    assert store.get("a") == "agent-1"
    assert store.get("b") == "agent-2"
    assert len(store) == 2


def test_pop_forgets_a_session():
    store = SessionStore(counter_factory())
    store.get("a")
    assert store.pop("a") == "agent-1"
    assert store.pop("a") is None
    assert store.get("a") == "agent-2"  # a fresh conversation


def test_expired_sessions_are_dropped():
    store = SessionStore(counter_factory(), ttl_seconds=0)
    store.get("a")
    time.sleep(0.01)
    assert store.get("a") == "agent-2"


def test_the_store_is_capped_and_evicts_the_least_recently_used():
    store = SessionStore(counter_factory(), max_sessions=2)
    store.get("a")
    store.get("b")
    store.get("a")  # refreshes 'a', making 'b' the oldest
    store.get("c")

    assert "b" not in store
    assert "a" in store and "c" in store
    assert len(store) == 2
