from ytt.tq import QueueSnapshot


class FakeQueue:
    """Queue stand-in: no worker thread, no network/GPU."""

    def __init__(self):
        self.paused = False
        self.current_title = None

    def snapshot(self):
        return QueueSnapshot(
            paused=self.paused, current=None, current_title=self.current_title,
            stage="idle", done=0, total=0, queued=[], log=[],
        )

    def stop(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def start(self):
        pass

    def enqueue(self, job):
        return "queued"

    def stage_of(self, video_id):
        return None

    def cancel_project(self, slug):
        return []
