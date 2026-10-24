import sys
import time

from threading import Thread


class SoundController:

    def __init__(self):
        self.enabled = True

    def play(self, pattern):
        if pattern is None:
            return None
        self._play(pattern)
        return pattern

    def _play(self, pattern):
        if not self.enabled or not pattern:
            return None

        thread = Thread(
            target=self._run,
            args=(pattern,),
            daemon=True
        )
        thread.start()
        return pattern

    def _run(self, pattern):
        if sys.platform.startswith("win"):
            try:
                import winsound
            except ImportError:
                winsound = None

            if winsound is None:
                for _, duration in pattern:
                    time.sleep(duration / 1000)
                return

            for frequency, duration in pattern:
                winsound.Beep(int(frequency), int(duration))
            return

        for _, duration in pattern:
            print("\a", end="", flush=True)
            time.sleep(max(0, float(duration)) / 1000)

    def complete(self):
        return self._play([
            (800, 150),
            (1000, 200)
        ])

    def play_complete(self):
        return self.complete()

    def error(self):
        return self._play([
            (1200, 300),
            (1200, 300),
            (1200, 300)
        ])

    def play_error(self):
        return self.error()

    def stop(self):
        self.enabled = False

    def resume(self):
        self.enabled = True