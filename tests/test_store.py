import threading
import unittest

from ddpp.store import PasswordStore, Watcher


class FakeClock:
    def __init__(self):
        self.value = 1_700_000_000.0

    def __call__(self):
        return self.value


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = PasswordStore(self.clock)

    def test_reads_info_and_final_burn(self):
        token = self.store.store(60, 2, "Pass123")
        self.assertEqual(len(token), 32)
        watcher = Watcher()
        self.store.watch(token, watcher)
        self.assertEqual(self.store.info(token), (2, 60))
        self.assertEqual(self.store.retrieve(token), "Pass123")
        self.assertEqual(self.store.info(token), (1, 60))
        self.assertFalse(watcher.event.is_set())
        self.assertEqual(self.store.retrieve(token), "Pass123")
        self.assertTrue(watcher.event.is_set())
        self.assertTrue(watcher.notice.startswith("210 BURNED " + token))
        self.assertIsNone(self.store.retrieve(token))
        self.assertIsNone(self.store.info(token))

    def test_expiry_checked_even_without_sweep(self):
        token = self.store.store(60, 1, "Pass123")
        watcher = Watcher()
        self.store.watch(token, watcher)
        self.clock.value += 60
        self.assertIsNone(self.store.retrieve(token))
        self.assertTrue(watcher.event.is_set())
        self.assertTrue(watcher.notice.startswith("220 EXPIRED " + token))

    def test_sweep_expires_unread_entry(self):
        token = self.store.store(60, 1, "Pass123")
        watcher = Watcher()
        self.store.watch(token, watcher)
        self.clock.value += 61
        self.assertEqual(self.store.sweep(), 1)
        self.assertIsNone(self.store.info(token))
        self.assertTrue(watcher.event.is_set())

    def test_atomic_single_read_race(self):
        for _ in range(30):
            token = self.store.store(60, 1, "Pass123")
            barrier = threading.Barrier(3)
            results = []

            def retrieve():
                barrier.wait()
                results.append(self.store.retrieve(token))

            threads = [threading.Thread(target=retrieve) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()
            self.assertCountEqual(results, ["Pass123", None])


if __name__ == "__main__":
    unittest.main()
