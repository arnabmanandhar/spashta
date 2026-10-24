import threading

from spashta.store import Store


def test_store_reads_and_writes_from_many_threads(tmp_path):
    store = Store(tmp_path / "t.db")
    errors = []

    def work(n):
        try:
            for i in range(200):
                url = f"https://example.com/{n}/{i}"
                store.seen(url)
                store.has_article(url)
                store.mark_seen(url, None)
        except Exception as e:  # noqa: BLE001 - the test collects any failure
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert all(store.seen(f"https://example.com/{n}/199") for n in range(6))


def test_seen_url_without_stored_article_is_fetched_again(tmp_path):
    store = Store(tmp_path / "t.db")
    store.mark_seen("https://example.com/a", "missing-id")  # run stopped before indexing
    store.mark_seen("https://example.com/b", None)  # skipped as too short
    assert not store.seen("https://example.com/a")
    assert store.seen("https://example.com/b")
